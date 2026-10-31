"""
Secure Key Management System for Cortex AI IDE
- OS Keyring (Windows Credential Manager / macOS Keychain / Secret Service) as PRIMARY storage
- Encrypted file as BACKUP storage
- Auto-re-prompt on decryption failure
- Export/import for migration

Security: Keys are encrypted with a master secret sourced from the OS
keyring or a user-provided passphrase, never persisted in clear text.
"""

import os
import json
import base64
import hashlib
import hmac
import secrets
import socket
import subprocess
import sys
import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from src.utils.logger import get_logger

log = get_logger("key_manager")

class KeyManager:
    """
    Manages API keys.

    Storage priority:
    1. OS Keyring (Windows Credential Manager, macOS Keychain, Linux Secret
       Service) — PRIMARY
    2. Encrypted file (~/.cortex/keys.enc) — BACKUP
    3. Auto-re-prompt on failure
    
    Security:
    - Keys encrypted with AES-256-GCM
    - Master key from the OS keystore; a machine-derived fallback is only
      used to unlock an existing vault, never to create a new one
    - Random salt per encryption
    - Rate limiting on decryption attempts
    """
    
    # Security constants
    SALT_SIZE = 32
    NONCE_SIZE = 12
    KEY_SIZE = 32
    PBKDF2_ITERATIONS = 600000  # OWASP 2023 recommendation

    # Keystore entry name for the vault master secret
    MASTER_SECRET_TARGET = "cortex_master_secret"

    # Service and username every Linux build has used for the Secret Service
    # entries. The vault master secret was written under exactly these names
    # before the open-source merge, so they must not change: a different
    # lookup finds nothing, and a fresh secret cannot open an old vault.
    KEYRING_SERVICE = "cortex"
    KEYRING_MASTER_USERNAME = "master_secret"

    # Set to 1 to accept a machine-derived master key on a machine that has
    # no OS keystore. Off by default: see _resolve_master_secret().
    ALLOW_INSECURE_KEYSTORE_ENV = "CORTEX_ALLOW_INSECURE_KEYSTORE"
    
    # Rate limiting
    MAX_DECRYPT_ATTEMPTS = 10
    DECRYPT_RATE_LIMIT_WINDOW = 60
    
    # Provider name mapping
    PROVIDER_MAP = {
        "mimo": "mimo",
        "deepseek": "deepseek",
        "openai": "openai",
        "openrouter": "openrouter",
        "alibaba": "alibaba",
        "siliconflow": "siliconflow",
        "google": "google",
    }

    # Secrets read through get_key() that are not model providers. They are
    # stored under the same "<name>_api_key" name as a provider, so a reset
    # has to clear them too.
    EXTRA_KEY_NAMES = ("telegram", "serpapi", "brave")
    
    def __init__(self):
        self._config_dir = self._resolve_config_dir()
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._keys_file = self._config_dir / "keys.enc"
        self._salt_file = self._config_dir / "keys.salt"
        self._integrity_file = self._config_dir / "keys.hmac"
        self._cache: Dict[str, str] = {}
        self._cache_ttl: Dict[str, datetime] = {}
        self._CACHE_DURATION = timedelta(minutes=5)
        # Parsed keys.enc, memoized. See _load_encrypted_keys().
        self._vault_cache: Optional[Dict[str, Any]] = None
        
        # Rate limiting
        self._decrypt_attempts: List[float] = []
        
        # Where the vault key came from, and whether it may be written to
        # disk at all. Both are set for real in _derive_master_key().
        self._master_key_source = "unknown"
        self._vault_persist_allowed = False

        # Secret Service backend (python `keyring`), resolved lazily once.
        # Must be set before _derive_master_key() runs, because that is the
        # first call that reaches for it.
        self._keyring_checked = False
        self._keyring = None
        self._keyring_backend_name = "unavailable"

        # Initialize master key
        self._master_key = self._derive_master_key()

    @staticmethod
    def _resolve_config_dir() -> Path:
        """Directory holding keys.enc, keys.salt and keys.hmac.

        Earlier Linux builds used $XDG_CONFIG_HOME/cortex when that variable
        was set. Honouring it again keeps such a vault reachable, but a vault
        already sitting in ~/.cortex wins over an empty XDG directory so that
        setting the variable later cannot hide keys that are already stored.
        """
        legacy = Path.home() / ".cortex"
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        if not xdg or not os.path.isabs(xdg):
            return legacy
        xdg_dir = Path(xdg) / "cortex"
        if (xdg_dir / "keys.enc").exists():
            return xdg_dir
        if (legacy / "keys.enc").exists():
            return legacy
        return xdg_dir

    @staticmethod
    def _chmod_private(path: Path) -> None:
        """Restrict a credential file to its owner. No-op on Windows."""
        if os.name == 'nt':
            return
        try:
            os.chmod(path, 0o600)
        except OSError as e:
            log.warning(f"[Security] Could not restrict permissions on {path}: {e}")

    # ─── Secret Service backend ───────────────────────────────────────────

    def _keyring_backend(self):
        """Resolve the `keyring` library once. Returns the module, or None
        when no usable Secret Service / KWallet backend exists (headless
        session, missing secretstorage, no DBus).

        This is the route every Linux build used before the open-source
        merge. It is tried before the `secret-tool` CLI because an existing
        vault was encrypted with the master secret this library holds, and
        a different tool is not guaranteed to find the same entry.
        """
        if self._keyring_checked:
            return self._keyring
        self._keyring_checked = True
        try:
            import keyring
            backend = keyring.get_keyring()
            priority = getattr(backend, "priority", 0)
            backend_type = type(backend).__module__ + "." + type(backend).__name__
            if priority <= 0 or "fail" in backend_type.lower():
                log.info(f"[Security] No usable OS keystore backend ({backend_type}); "
                         f"falling back to the encrypted vault")
                self._keyring = None
            else:
                self._keyring = keyring
                self._keyring_backend_name = backend_type
                log.info(f"[Security] OS keystore backend: {backend_type}")
        except Exception as e:
            log.info(f"[Security] keyring library unavailable ({e}); "
                     f"falling back to the encrypted vault")
            self._keyring = None
        return self._keyring
        
    def _derive_master_key(self) -> bytes:
        """
        Derive the vault master key from the resolved master secret.

        The secret itself is chosen in _resolve_master_secret(); this method
        only turns it into a key and records whether the result is fit to be
        written to disk. A session-only key must never produce a keys.enc
        file, because nothing on the next launch could reopen it.
        """
        master_secret, source = self._resolve_master_secret()
        self._master_key_source = source
        self._vault_persist_allowed = source != "session_only"

        # Ensure master_secret is string
        if isinstance(master_secret, bytes):
            master_secret = master_secret.decode('utf-8', errors='ignore')
        
        # Load or generate salt
        salt = self._load_or_generate_salt()
        
        # Derive key using PBKDF2 (OWASP 2023: 600,000 iterations)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=self.KEY_SIZE,
            salt=salt,
            iterations=self.PBKDF2_ITERATIONS,
        )
        return kdf.derive(master_secret.encode('utf-8'))

    def _resolve_master_secret(self) -> tuple:
        """
        Choose the master secret, and say where it came from.

        Only the OS keystore holds something that is actually secret, so it
        is the only source that protects the vault. The machine fingerprint
        (COMPUTERNAME, USERNAME, PROCESSOR_IDENTIFIER, home directory) is
        public data, and a key derived from it makes keys.enc reversible by
        anyone who copies keys.enc and keys.salt. It is therefore used in
        exactly two cases, both loud:

          * an existing vault already sits on disk and must stay readable;
          * the owner explicitly opted in with CORTEX_ALLOW_INSECURE_KEYSTORE=1.

        Otherwise the master key is random and never leaves this process, so
        Cortex writes no vault at all rather than filling one with ciphertext
        that only looks encrypted.
        """
        keystore_secret = self._get_os_keyring_secret()
        if keystore_secret:
            log.info("[Security] Master key from OS keystore")
            return keystore_secret, "os_keyring"

        if self._keys_file.exists():
            log.error(
                "[Security] No OS keystore; unlocking the existing vault with the "
                "machine fingerprint. That key is guessable, so keys.enc is "
                "obfuscated rather than protected. Install the platform keystore "
                "to fix this (libsecret's secret-tool on Linux)."
            )
            return self._get_machine_fingerprint(), "legacy_machine_fingerprint"

        if os.environ.get(self.ALLOW_INSECURE_KEYSTORE_ENV) == "1":
            log.error(
                "[Security] %s=1 - encrypting keys with a machine-derived key. "
                "Anyone holding keys.enc and keys.salt can decrypt them.",
                self.ALLOW_INSECURE_KEYSTORE_ENV,
            )
            return self._get_machine_fingerprint(), "machine_fingerprint_opt_in"

        log.error(
            "[Security] No OS keystore available, so no vault key can be kept "
            "secret. API keys will be used for this session only and not written "
            "to disk. Install the platform keystore, or set %s=1 to accept "
            "machine-derived keys on disk.",
            self.ALLOW_INSECURE_KEYSTORE_ENV,
        )
        return secrets.token_hex(32), "session_only"
    
    def _get_os_keyring_secret(self) -> Optional[str]:
        """
        Get master secret from OS Keyring.
        Windows Credential Manager, macOS Keychain, Linux Secret Service.
        """
        if os.name == 'nt':
            # ctypes (win_cred), NOT pywin32. Bug history: win32cred.CredRead
            # failed inside frozen builds while CredWrite worked, every launch
            # of the installed app silently created a NEW master secret, which
            # made keys.enc undecryptable and 'lost' all saved keys. The bare
            # except here hid the real error for weeks.
            from src.core import win_cred
            target = self.MASTER_SECRET_TARGET
            try:
                blob = win_cred.cred_read(target)
                if blob is not None:
                    secret = win_cred.decode_blob(blob)
                    if secret:
                        return secret
                    log.warning("[Security] Master secret credential exists but is empty, recreating")
            except OSError as e:
                # A read FAILURE is not "missing", creating a new master here
                # would orphan every previously encrypted secret. Return None
                # and let _resolve_master_secret() decide, loudly, what to do.
                log.error(f"[Security] Master secret READ failed (not missing): {e}")
                return None
            try:
                secret = secrets.token_hex(32)
                win_cred.cred_write(target, secret,
                                    comment='Cortex AI IDE master encryption secret')
                log.info("[Security] Created new master secret in Windows Credential Manager")
                return secret
            except OSError as e:
                log.error(f"[Security] Master secret WRITE failed: {e}")
                return None

        if sys.platform == 'darwin':
            return self._macos_keychain_secret()
        return self._secret_service_secret()

    def _macos_keychain_secret(self) -> Optional[str]:
        """Read the vault master secret from the macOS Keychain, creating it once."""
        target = self.MASTER_SECRET_TARGET
        try:
            read = subprocess.run(
                ["security", "find-generic-password", "-s", target, "-a", "cortex", "-w"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.error(f"[Security] Keychain READ failed: {e}")
            return None
        if read.returncode == 0 and read.stdout.strip():
            return read.stdout.strip()

        secret = secrets.token_hex(32)
        try:
            created = subprocess.run(
                ["security", "add-generic-password", "-U", "-s", target, "-a", "cortex",
                 "-w", secret],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.error(f"[Security] Keychain WRITE failed: {e}")
            return None
        if created.returncode != 0:
            log.error(f"[Security] Keychain WRITE failed: {created.stderr.strip()}")
            return None
        log.info("[Security] Created new master secret in the macOS Keychain")
        return secret

    def _secret_service_secret(self) -> Optional[str]:
        """Read the vault master secret from the Secret Service, creating it once.

        Two routes reach the same Secret Service, in this order:

          * the `keyring` library, which is what earlier Linux builds used,
            so it finds the secret an existing vault was encrypted with;
          * libsecret's `secret-tool` CLI, for environments without the
            Python package.

        A read that FAILS is not a missing entry: creating a fresh master
        secret there would orphan every stored key, so None is returned and
        _resolve_master_secret() decides what to do.
        """
        keyring = self._keyring_backend()
        if keyring is not None:
            try:
                secret = keyring.get_password(
                    self.KEYRING_SERVICE, self.KEYRING_MASTER_USERNAME
                )
            except Exception as e:
                log.error(f"[Security] Secret Service READ failed (not missing): {e}")
                return None
            if secret:
                return secret

            secret = secrets.token_hex(32)
            try:
                keyring.set_password(
                    self.KEYRING_SERVICE, self.KEYRING_MASTER_USERNAME, secret
                )
            except Exception as e:
                log.error(f"[Security] Secret Service WRITE failed: {e}")
                return None
            log.info("[Security] Created new master secret in the Secret Service")
            return secret

        return self._secret_tool_secret()

    def _secret_tool_secret(self) -> Optional[str]:
        """Read or create the master secret through libsecret's CLI.

        Only used when the `keyring` library is not installed. The entry
        uses its own attributes rather than the library's service/username
        mapping, so the two routes keep separate records on purpose.
        """
        attributes = ["service", "cortex", "purpose", "master_secret"]
        try:
            read = subprocess.run(
                ["secret-tool", "lookup", *attributes],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if read.returncode == 0 and read.stdout.strip():
            return read.stdout.strip()

        secret = secrets.token_hex(32)
        try:
            created = subprocess.run(
                ["secret-tool", "store", "--label=Cortex AI IDE master secret", *attributes],
                input=secret, capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.error(f"[Security] Secret Service WRITE failed: {e}")
            return None
        if created.returncode != 0:
            log.error(f"[Security] Secret Service WRITE failed: {created.stderr.strip()}")
            return None
        log.info("[Security] Created new master secret in the Secret Service")
        return secret
    
    def _get_machine_fingerprint(self) -> str:
        """Get machine-specific fingerprint for key derivation.

        NOT a secret: every component is public or trivially guessable, so a
        key derived from this string only obfuscates. See
        _resolve_master_secret() for the two cases where it may be used.

        The components differ per platform on purpose. COMPUTERNAME,
        USERNAME and PROCESSOR_IDENTIFIER do not exist on Linux, so reading
        them there produced "|||/home/<user>", a different string from the
        one earlier builds used (/etc/machine-id, hostname, USER, home).
        That mismatch alone made every vault entry on those machines
        impossible to decrypt.
        """
        if os.name == 'nt':
            components = [
                os.environ.get('COMPUTERNAME', ''),
                os.environ.get('USERNAME', ''),
                os.environ.get('PROCESSOR_IDENTIFIER', ''),
                str(Path.home()),
            ]
            return '|'.join(components)

        machine_id = ''
        for machine_id_path in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
            try:
                machine_id = Path(machine_id_path).read_text().strip()
                if machine_id:
                    break
            except OSError:
                continue
        components = [
            machine_id,
            socket.gethostname(),
            os.environ.get('USER', ''),
            str(Path.home()),
        ]
        return '|'.join(components)
    
    def _load_or_generate_salt(self) -> bytes:
        """Load existing salt or generate new one."""
        if self._salt_file.exists():
            try:
                salt = self._salt_file.read_bytes()
                if len(salt) == self.SALT_SIZE:
                    return salt
            except Exception as e:
                # Falling through regenerates the salt, which makes every key
                # already encrypted with the old one unreadable. Never silent.
                log.warning(f"[KeyManager] could not read the salt file {self._salt_file}: {e}")
        
        # Generate new salt
        salt = secrets.token_bytes(self.SALT_SIZE)
        try:
            self._salt_file.write_bytes(salt)
            if os.name == 'nt':
                import stat
                os.chmod(self._salt_file, stat.S_IREAD | stat.S_IWRITE)
            else:
                self._chmod_private(self._salt_file)
        except Exception as e:
            log.warning(f"[Security] Could not save salt file: {e}")
        
        return salt
    
    def _encrypt(self, plaintext: str) -> tuple:
        """Encrypt plaintext with AES-256-GCM."""
        nonce = secrets.token_bytes(self.NONCE_SIZE)
        salt = secrets.token_bytes(self.SALT_SIZE)
        
        # Derive encryption key
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=self.KEY_SIZE,
            salt=salt,
            iterations=10000,
        )
        enc_key = kdf.derive(self._master_key + salt)
        
        # Encrypt
        aesgcm = AESGCM(enc_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
        
        return nonce, ciphertext, salt
    
    def _decrypt(self, nonce: bytes, ciphertext: bytes, salt: bytes) -> Optional[str]:
        """Decrypt ciphertext with AES-256-GCM."""
        # Rate limiting
        if not self._check_rate_limit():
            log.warning("[Security] Decryption rate limit exceeded")
            return None
        
        try:
            # Derive decryption key
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=self.KEY_SIZE,
                salt=salt,
                iterations=10000,
            )
            dec_key = kdf.derive(self._master_key + salt)
            
            # Decrypt
            aesgcm = AESGCM(dec_key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            
            return plaintext.decode('utf-8')
            
        except Exception as e:
            log.warning(f"[Security] Decryption failed: {type(e).__name__}")
            return None
    
    def _check_rate_limit(self) -> bool:
        """Check if decryption attempts are within rate limit."""
        now = time.time()
        self._decrypt_attempts = [
            t for t in self._decrypt_attempts
            if now - t < self.DECRYPT_RATE_LIMIT_WINDOW
        ]
        if len(self._decrypt_attempts) >= self.MAX_DECRYPT_ATTEMPTS:
            return False
        self._decrypt_attempts.append(now)
        return True
    
    def _compute_hmac(self, data: bytes) -> str:
        """Compute HMAC-SHA256 for integrity verification."""
        return hmac.new(
            self._master_key,
            data,
            hashlib.sha256
        ).hexdigest()
    
    # ─── Public API ──────────────────────────────────────────────────────
    
    def store_key(self, provider: str, api_key: str, 
                  storage_backend: str = "auto") -> bool:
        """
        Store an API key securely.
        1. Store in OS Keyring (PRIMARY)
        2. Store in encrypted file (BACKUP)
        """
        try:
            # Clean the key — strip null bytes, whitespace, quotes
            if api_key:
                api_key = api_key.replace('\x00', '').replace('\u0000', '').replace(' ', '').replace('\n', '').replace('\r', '')
            api_key = api_key.strip()
            api_key = api_key.strip("'\"")
            
            if not api_key:
                log.error(f"Empty API key provided for {provider}")
                return False
            
            log.info(f"[KeyManager] Storing key for {provider}")
            
            # Store in OS Keyring (PRIMARY), ctypes, not pywin32
            if os.name == 'nt':
                try:
                    from src.core import win_cred
                    win_cred.cred_write(f"cortex_{provider}_api_key", api_key,
                                        comment=f'Cortex AI API key for {provider}')
                    log.info(f"[KeyManager] Stored {provider} key in Windows Credential Manager")
                except OSError as e:
                    log.warning(f"[KeyManager] Failed to store in Windows Credential Manager: {e}")
            else:
                keyring = self._keyring_backend()
                if keyring is not None:
                    try:
                        keyring.set_password(
                            self.KEYRING_SERVICE, f"{provider}_api_key", api_key
                        )
                        log.info(f"[KeyManager] Stored {provider} key in the OS keystore")
                    except Exception as e:
                        # The encrypted vault below is still written, so the
                        # key is not lost; only the primary copy failed.
                        log.warning(f"[KeyManager] Failed to store in the OS keystore: {e}")
            
            # Store in encrypted file (BACKUP)
            self._store_encrypted_file(provider, api_key)
            
            # Cache the key
            self._cache[provider] = api_key
            self._cache_ttl[provider] = datetime.now()
            
            log.info(f"[KeyManager] Successfully stored key for {provider}")
            return True
            
        except Exception as e:
            log.error(f"[KeyManager] Failed to store key for {provider}: {e}")
            return False
    
    def get_key(self, provider: str, force_refresh: bool = False) -> Optional[str]:
        """
        Retrieve an API key.
        1. Check cache
        2. Try OS Keyring (PRIMARY)
        3. Try encrypted file (BACKUP)
        4. Auto-re-prompt on failure
        """
        # Clear cache if force refresh
        if force_refresh and provider in self._cache:
            del self._cache[provider]
            del self._cache_ttl[provider]
        
        # Check cache
        if provider in self._cache:
            if datetime.now() - self._cache_ttl[provider] < self._CACHE_DURATION:
                return self._cache[provider]
            else:
                del self._cache[provider]
                del self._cache_ttl[provider]
        
        # Try OS Keyring (PRIMARY)
        key = self._get_os_keyring_key(provider)
        
        # Try encrypted file (BACKUP)
        if not key:
            key = self._get_encrypted_file(provider)
        
        if key:
            # Sanitize retrieved key (strip null bytes, spaces, etc.)
            key = key.replace('\x00', '').replace('\u0000', '').replace('\n', '').replace('\r', '')
            key = key.strip()
            # Cache the key
            self._cache[provider] = key
            self._cache_ttl[provider] = datetime.now()
            log.debug(f"[KeyManager] Retrieved {provider} key")
        else:
            log.debug(f"[KeyManager] No key found for {provider}")
        
        return key
    
    def _get_os_keyring_key(self, provider: str) -> Optional[str]:
        """Get key from the OS keystore.

        Windows goes through ctypes (see win_cred.py: pywin32's CredRead
        broke in frozen builds and the old bare `except: return None` hid it
        entirely). Every other platform goes through the `keyring` library,
        which is where the Linux build has always kept provider keys, so
        keys saved before the merge are still found.
        """
        if os.name == 'nt':
            target = f"cortex_{provider}_api_key"
            try:
                from src.core import win_cred
                blob = win_cred.cred_read(target)
                if blob is None:
                    return None
                key = win_cred.decode_blob(blob)
                return key.strip("'\"") if key else None
            except OSError as e:
                log.warning(f"[KeyManager] Credential READ failed for {provider}: {e}")
                return None

        keyring = self._keyring_backend()
        if keyring is None:
            return None
        try:
            key = keyring.get_password(self.KEYRING_SERVICE, f"{provider}_api_key")
        except Exception as e:
            log.warning(f"[KeyManager] Secret Service READ failed for {provider}: {e}")
            return None
        return key.strip("'\"") if key else None
    
    def _get_encrypted_file(self, provider: str) -> Optional[str]:
        """Get key from encrypted file (BACKUP)."""
        try:
            if not self._keys_file.exists():
                return None

            # An entry encrypted with an old master key can never decrypt
            # (InvalidTag). Without this cache, every provider load retried
            # it, spamming warnings and eating the decrypt rate-limit
            # budget that legitimate keys then need.
            if not hasattr(self, '_undecryptable_backup'):
                self._undecryptable_backup: set = set()
            if provider in self._undecryptable_backup:
                return None

            keys = self._load_encrypted_keys()
            if provider not in keys:
                return None

            key_data = keys[provider]

            # Decrypt the key
            nonce = base64.b64decode(key_data['nonce'])
            ciphertext = base64.b64decode(key_data['ciphertext'])
            salt = base64.b64decode(key_data['salt'])

            result = self._decrypt(nonce, ciphertext, salt)
            if result is None:
                self._undecryptable_backup.add(provider)
                log.info(f"[KeyManager] Backup entry for {provider} is undecryptable "
                         f"(stale master key), skipping retries this session")
            return result

        except Exception as e:
            log.warning(f"[KeyManager] Failed to read encrypted key for {provider}: {e}")
            return None
    
    def _store_encrypted_file(self, provider: str, api_key: str) -> bool:
        """Store key in encrypted file (BACKUP)."""
        if not self._vault_persist_allowed:
            # No keystore and no existing vault: the only key we have is
            # random and dies with the process. Writing now would leave a
            # keys.enc nothing could reopen.
            log.error(
                "[KeyManager] Not writing keys.enc: no OS keystore is available, "
                "so the %s key stays in memory for this session only", provider,
            )
            return False
        try:
            keys = self._load_encrypted_keys()
            
            # Encrypt the key
            nonce, ciphertext, salt = self._encrypt(api_key)
            
            # Store encrypted key
            keys[provider] = {
                'nonce': base64.b64encode(nonce).decode('ascii'),
                'ciphertext': base64.b64encode(ciphertext).decode('ascii'),
                'salt': base64.b64encode(salt).decode('ascii'),
                'created_at': datetime.now().isoformat(),
                'algorithm': 'AES-256-GCM',
            }
            
            # Save
            self._keys_file.write_text(json.dumps(keys, indent=2))
            self._chmod_private(self._keys_file)
            self._vault_cache = None
            
            # Update HMAC
            data_to_sign = json.dumps(keys, sort_keys=True).encode('utf-8')
            hmac_value = self._compute_hmac(data_to_sign)
            self._integrity_file.write_text(hmac_value)
            self._chmod_private(self._integrity_file)
            
            return True
            
        except Exception as e:
            log.error(f"[KeyManager] Failed to store encrypted key for {provider}: {e}")
            return False
    
    def _load_encrypted_keys(self) -> Dict[str, Any]:
        """Load encrypted keys from file, parsing it once per write.

        The Settings panel asks for one key per provider row, and every miss
        used to read and re-parse this whole file, so the cost of opening the
        panel grew with the number of providers the server publishes. On
        Linux it was paid for every row, because _get_os_keyring_key() is a
        no-op there and every lookup falls through to this file.

        The file is written only by _store_encrypted_file(), delete_key() and
        import_credentials(), each of which drops this cache, and nothing
        outside this class reads it, so the memo cannot outlive a change. A
        copy is handed out so a caller that mutates the mapping before a write
        cannot leave the memo ahead of the file if that write fails.
        """
        if self._vault_cache is not None:
            return dict(self._vault_cache)
        if not self._keys_file.exists():
            self._vault_cache = {}
            return {}
        try:
            content = self._keys_file.read_text()
            parsed = json.loads(content)
            self._vault_cache = parsed if isinstance(parsed, dict) else {}
        except Exception:
            self._vault_cache = {}
        return self._vault_cache
    
    def delete_key(self, provider: str) -> bool:
        """Delete a stored API key from all backends."""
        try:
            # Remove from cache
            if provider in self._cache:
                del self._cache[provider]
                del self._cache_ttl[provider]
            
            deleted = False
            
            # Delete from OS Keyring (ctypes, see win_cred.py)
            if os.name == 'nt':
                try:
                    from src.core import win_cred
                    if win_cred.cred_delete(f"cortex_{provider}_api_key"):
                        deleted = True
                        log.info(f"[KeyManager] Deleted {provider} key from Windows Credential Manager")
                except OSError as e:
                    log.warning(f"[KeyManager] Credential DELETE failed for {provider}: {e}")
            else:
                keyring = self._keyring_backend()
                if keyring is not None:
                    try:
                        keyring.delete_password(
                            self.KEYRING_SERVICE, f"{provider}_api_key"
                        )
                        deleted = True
                        log.info(f"[KeyManager] Deleted {provider} key from the OS keystore")
                    except Exception as e:
                        # A missing entry raises too, so this is a debug note,
                        # not a warning: the vault copy below still runs.
                        log.debug(f"[KeyManager] OS keystore delete for {provider}: {e}")
            
            # Delete from encrypted file
            try:
                keys = self._load_encrypted_keys()
                if provider in keys:
                    del keys[provider]
                    self._keys_file.write_text(json.dumps(keys, indent=2))
                    self._chmod_private(self._keys_file)
                    self._vault_cache = None
                    deleted = True
                    log.info(f"[KeyManager] Deleted {provider} key from encrypted file")
            except Exception as e:
                # The vault copy may already be gone, leaving the key in the
                # encrypted file after the caller was told it was deleted.
                log.warning(f"[KeyManager] could not remove {provider} from the encrypted file: {e}")
            
            return deleted
            
        except Exception as e:
            log.error(f"[KeyManager] Failed to delete key for {provider}: {e}")
            return False
    
    def clear_cache(self) -> None:
        """Clear the key cache."""
        self._cache.clear()
        self._cache_ttl.clear()
    
    # ─── Recovery & Migration ──────────────────────────────
    

    
    def import_credentials(self, import_path: str) -> bool:
        """Import encrypted credentials from JSON file."""
        try:
            import_data = json.loads(Path(import_path).read_text())
            
            if import_data.get('version') != 1:
                log.error("[Import] Invalid import file version")
                return False
            
            keys = import_data.get('keys', {})
            if not keys:
                log.warning("[Import] No keys in import file")
                return False
            
            if 'salt' in import_data:
                salt = base64.b64decode(import_data['salt'])
                self._salt_file.write_bytes(salt)
            
            existing_keys = self._load_encrypted_keys()
            imported_count = 0
            for provider, key_data in keys.items():
                if provider not in existing_keys:
                    existing_keys[provider] = key_data
                    imported_count += 1
            
            self._keys_file.write_text(json.dumps(existing_keys, indent=2))
            self._chmod_private(self._keys_file)
            self._vault_cache = None
            
            data_to_sign = json.dumps(existing_keys, sort_keys=True).encode('utf-8')
            hmac_value = self._compute_hmac(data_to_sign)
            self._integrity_file.write_text(hmac_value)
            self._chmod_private(self._integrity_file)
            
            self.clear_cache()
            
            log.info(f"[Import] Imported {imported_count} keys from {import_path}")
            return True
            
        except Exception as e:
            log.error(f"[Import] Failed: {e}")
            return False
    
    def reset_credentials(self) -> bool:
        """Reset all credentials.

        The keystore entries are cleared under every name store_key() can
        write, which is PROVIDER_MAP plus the non-provider secrets in
        EXTRA_KEY_NAMES. Iterating PROVIDER_MAP alone reported success
        while leaving the Telegram and web-search keys in the OS keystore.
        """
        try:
            if self._keys_file.exists():
                self._keys_file.unlink()
                self._vault_cache = None
            if self._salt_file.exists():
                self._salt_file.unlink()
            if self._integrity_file.exists():
                self._integrity_file.unlink()

            targets = [
                f"{name}_api_key"
                for name in list(self.PROVIDER_MAP) + list(self.EXTRA_KEY_NAMES)
            ]

            if os.name == 'nt':
                from src.core import win_cred
                for target in targets:
                    try:
                        win_cred.cred_delete(f"cortex_{target}")
                    except OSError as e:
                        log.warning(f"[Reset] Credential delete failed for {target}: {e}")
            else:
                keyring = self._keyring_backend()
                if keyring is not None:
                    for target in targets:
                        try:
                            keyring.delete_password(self.KEYRING_SERVICE, target)
                        except Exception as e:
                            log.debug(f"[Reset] OS keystore delete for {target}: {e}")
            
            self.clear_cache()
            log.info("[Reset] All credentials reset")
            return True
            
        except Exception as e:
            log.error(f"[Reset] Failed: {e}")
            return False
    

    
    def get_security_info(self) -> Dict[str, Any]:
        """Get security configuration information."""
        return {
            'encryption': 'AES-256-GCM',
            'primary_storage': 'OS keystore (Credential Manager / Keychain / Secret Service)',
            'backup_storage': 'Encrypted file (keys.enc)',
            'master_key_source': self._master_key_source,
            'backup_storage_writable': self._vault_persist_allowed,
            'keyring_backend': self._keyring_backend_name,
            'salt_size': self.SALT_SIZE,
            'nonce_size': self.NONCE_SIZE,
            'key_size': self.KEY_SIZE,
            'rate_limit': {
                'max_attempts': self.MAX_DECRYPT_ATTEMPTS,
                'window_seconds': self.DECRYPT_RATE_LIMIT_WINDOW,
            },
        }


def get_key_manager() -> KeyManager:
    """Get the singleton KeyManager instance."""
    if not hasattr(get_key_manager, '_instance'):
        get_key_manager._instance = KeyManager()
    return get_key_manager._instance
