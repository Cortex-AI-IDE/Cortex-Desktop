"""
Runtime hook: configure TLS CA certificate bundle for frozen builds.

PyInstaller does not always bundle certifi's cacert.pem correctly.
This hook ensures requests/urllib3 can find the CA bundle by:
1. Setting REQUESTS_CA_BUNDLE environment variable
2. Patching certifi.where() if needed
"""

import os
import sys


def _configure_certifi():
    """Find and configure the CA certificate bundle for frozen builds."""
    if not getattr(sys, 'frozen', False):
        return  # Not a frozen build, nothing to do

    # Skip if already configured by user
    if os.environ.get('REQUESTS_CA_BUNDLE'):
        return

    # Try to import certifi and get the bundled path
    try:
        import certifi
        ca_path = certifi.where()
        if os.path.isfile(ca_path):
            os.environ['REQUESTS_CA_BUNDLE'] = ca_path
            return
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: search for cacert.pem in known locations
    search_paths = []

    # PyInstaller onefile: _MEIPASS temp directory
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        search_paths.append(os.path.join(meipass, 'certifi', 'cacert.pem'))
        search_paths.append(os.path.join(meipass, 'cacert.pem'))

    # PyInstaller onedir: next to executable
    exe_dir = os.path.dirname(sys.executable)
    search_paths.append(os.path.join(exe_dir, '_internal', 'certifi', 'cacert.pem'))
    search_paths.append(os.path.join(exe_dir, 'certifi', 'cacert.pem'))

    # Check each path
    for path in search_paths:
        if os.path.isfile(path):
            os.environ['REQUESTS_CA_BUNDLE'] = path
            return

    # Last resort: build a bundle from the Windows certificate store.
    #
    # This branch used to create an ssl context, discard it, and fall through
    # to `pass`, so it configured nothing at all. requests then asked certifi
    # itself, got the path that does not exist, and raised
    #   Could not find a suitable TLS CA certificate bundle, invalid path: ...
    # on every single request. A fallback that silently does nothing is worse
    # than no fallback, because it reads like the case is handled.
    #
    # requests wants a file, so write the roots Windows already trusts into
    # one and point at that. It only runs when the bundled pem is genuinely
    # missing, which now also means the build is broken and should be fixed
    # by collect_data_files('certifi') in cortex.spec.
    try:
        import ssl
        import tempfile

        pem_parts = []
        for store in ("ROOT", "CA"):
            try:
                for cert_bytes, enc, trust in ssl.enum_certificates(store):
                    if enc != "x509_asn":
                        continue
                    # trust is True for "all purposes", or a tuple of OIDs.
                    # 1.3.6.1.5.5.7.3.1 is server authentication.
                    if trust is True or (
                        isinstance(trust, (tuple, list, set))
                        and "1.3.6.1.5.5.7.3.1" in trust
                    ):
                        pem_parts.append(ssl.DER_cert_to_PEM_cert(cert_bytes))
            except Exception:
                continue

        if pem_parts:
            fd, pem_path = tempfile.mkstemp(prefix="cortex_ca_", suffix=".pem")
            with os.fdopen(fd, "w", encoding="ascii") as fh:
                fh.write("".join(pem_parts))
            os.environ["REQUESTS_CA_BUNDLE"] = pem_path
            os.environ.setdefault("SSL_CERT_FILE", pem_path)
    except Exception:
        # Nothing else to try. Leave the environment alone so the failure is
        # the library's own clear TLS error rather than a half-set variable.
        pass


_configure_certifi()
