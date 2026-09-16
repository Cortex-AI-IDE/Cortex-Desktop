"""
Points Management System for Cortex AI IDE

Manages user points balance, consumption, and tracking.
Points are used to control AI token consumption based on performance mode.

Point System:
    $10  → 1,000 points  = 1,000,000 tokens
    $20  → 2,500 points  = 2,500,000 tokens
    $40  → 6,000 points  = 6,000,000 tokens
    
Point Consumption:
    Points Used = Actual Tokens Ã- Performance Mode Multiplier
    Efficient (0.3x):    1000 tokens Ã- 0.3 = 300 points
    Auto (1.0x):         1000 tokens Ã- 1.0 = 1000 points
    Performance (1.1x):  1000 tokens Ã- 1.1 = 1100 points
    Ultimate (1.6x):     1000 tokens Ã- 1.6 = 1600 points
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from src.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class PointsTransaction:
    """Represents a single points transaction."""
    transaction_id: str
    timestamp: float
    transaction_type: str  # 'purchase' or 'consumption'
    points: int  # Positive for purchase, negative for consumption
    description: str
    mode_multiplier: Optional[float] = None
    tokens_used: Optional[int] = None
    performance_mode: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "timestamp": self.timestamp,
            "type": self.transaction_type,
            "points": self.points,
            "description": self.description,
            "mode_multiplier": self.mode_multiplier,
            "tokens_used": self.tokens_used,
            "performance_mode": self.performance_mode,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PointsTransaction':
        return cls(
            transaction_id=data["transaction_id"],
            timestamp=data["timestamp"],
            transaction_type=data["type"],
            points=data["points"],
            description=data["description"],
            mode_multiplier=data.get("mode_multiplier"),
            tokens_used=data.get("tokens_used"),
            performance_mode=data.get("performance_mode"),
        )


@dataclass
class PointsBalance:
    """User's points balance and statistics."""
    balance: int = 0
    total_purchased: int = 0
    total_consumed: int = 0
    transactions: List[PointsTransaction] = field(default_factory=list)
    last_updated: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "balance": self.balance,
            "total_purchased": self.total_purchased,
            "total_consumed": self.total_consumed,
            "last_updated": self.last_updated,
            "transactions": [t.to_dict() for t in self.transactions[-100:]],  # Keep last 100
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PointsBalance':
        balance = cls(
            balance=data.get("balance", 0),
            total_purchased=data.get("total_purchased", 0),
            total_consumed=data.get("total_consumed", 0),
            last_updated=data.get("last_updated", 0.0),
        )
        balance.transactions = [
            PointsTransaction.from_dict(t) 
            for t in data.get("transactions", [])
        ]
        return balance


class PointsManager:
    """
    Manages user points for AI token consumption.
    
    Handles:
    - Points purchase and balance tracking
    - Point consumption based on performance mode
    - Transaction history
    - Balance persistence
    """
    
    # Pricing tiers: dollars → points
    PRICING_TIERS = {
        10: 1000,    # $10 = 1,000 points (1M tokens)
        20: 2500,    # $20 = 2,500 points (2.5M tokens)
        40: 6000,    # $40 = 6,000 points (6M tokens)
    }
    
    # Performance mode multipliers
    MODE_MULTIPLIERS = {
        "efficient": 0.3,
        "auto": 1.0,
        "performance": 1.1,
        "ultimate": 1.6,
    }
    
    def __init__(self):
        self._points_dir = Path.home() / ".cortex" / "points"
        self._balance_file = self._points_dir / "balance.json"
        self._balance = PointsBalance()
        self._load_balance()
    
    def _load_balance(self):
        """Load points balance from disk."""
        try:
            self._points_dir.mkdir(parents=True, exist_ok=True)
            if self._balance_file.exists():
                with open(self._balance_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._balance = PointsBalance.from_dict(data)
                log.info(f"[PointsManager] Loaded balance: {self._balance.balance} points")
            else:
                log.info("[PointsManager] No existing balance file, starting with 0 points")
        except Exception as e:
            log.error(f"[PointsManager] Failed to load balance: {e}")
            self._balance = PointsBalance()
    
    def _save_balance(self):
        """Save points balance to disk."""
        try:
            self._balance.last_updated = time.time()
            with open(self._balance_file, "w", encoding="utf-8") as f:
                json.dump(self._balance.to_dict(), f, indent=2)
        except Exception as e:
            log.error(f"[PointsManager] Failed to save balance: {e}")
    
    def get_balance(self) -> int:
        """Get current points balance."""
        return self._balance.balance
    
    def get_tokens_equivalent(self) -> int:
        """Get tokens equivalent of current balance (1 point = 1000 tokens)."""
        return self._balance.balance * 1000
    
    def purchase_points(self, dollars: int) -> int:
        """
        Purchase points with dollars.
        
        Args:
            dollars: Amount in USD (10, 20, or 40)
        
        Returns:
            Points purchased
            
        Raises:
            ValueError: If dollars is not a valid tier
        """
        if dollars not in self.PRICING_TIERS:
            raise ValueError(
                f"Invalid purchase amount: ${dollars}. "
                f"Valid tiers: {list(self.PRICING_TIERS.keys())}"
            )
        
        points = self.PRICING_TIERS[dollars]
        
        # Update balance
        self._balance.balance += points
        self._balance.total_purchased += points
        
        # Record transaction
        transaction = PointsTransaction(
            transaction_id=f"purchase_{int(time.time())}",
            timestamp=time.time(),
            transaction_type="purchase",
            points=points,
            description=f"Purchased ${dollars} = {points:,} points",
        )
        self._balance.transactions.append(transaction)
        
        self._save_balance()
        
        log.info(f"[PointsManager] Purchase: ${dollars} → {points:,} points. Balance: {self._balance.balance:,}")
        
        return points
    
    def estimate_cost(self, estimated_tokens: int, performance_mode: str) -> int:
        """
        Estimate points cost for a request.
        
        Args:
            estimated_tokens: Estimated token usage
            performance_mode: Mode name (efficient/auto/performance/ultimate)
        
        Returns:
            Estimated points cost
        """
        multiplier = self.MODE_MULTIPLIERS.get(performance_mode, 1.0)
        return int(estimated_tokens * multiplier)
    
    def can_afford(self, estimated_tokens: int, performance_mode: str) -> bool:
        """Check if user has enough points for a request."""
        cost = self.estimate_cost(estimated_tokens, performance_mode)
        return self._balance.balance >= cost
    
    def consume_points(self, actual_tokens: int, performance_mode: str) -> dict:
        """
        Consume points based on actual token usage and performance mode.
        
        Args:
            actual_tokens: Actual tokens used by AI response
            performance_mode: Mode used (efficient/auto/performance/ultimate)
        
        Returns:
            Dict with consumption details
            
        Raises:
            ValueError: If performance_mode is invalid
            InsufficientPointsError: If balance is insufficient
        """
        if performance_mode not in self.MODE_MULTIPLIERS:
            raise ValueError(f"Invalid performance mode: {performance_mode}")
        
        multiplier = self.MODE_MULTIPLIERS[performance_mode]
        points_cost = int(actual_tokens * multiplier)
        
        if self._balance.balance < points_cost:
            raise InsufficientPointsError(
                f"Insufficient points. Need {points_cost:,}, have {self._balance.balance:,}",
                required=points_cost,
                balance=self._balance.balance
            )
        
        # Update balance
        self._balance.balance -= points_cost
        self._balance.total_consumed += points_cost
        
        # Record transaction
        transaction = PointsTransaction(
            transaction_id=f"consume_{int(time.time())}",
            timestamp=time.time(),
            transaction_type="consumption",
            points=-points_cost,  # Negative for consumption
            description=f"{performance_mode} mode: {actual_tokens:,} tokens Ã- {multiplier}x",
            mode_multiplier=multiplier,
            tokens_used=actual_tokens,
            performance_mode=performance_mode,
        )
        self._balance.transactions.append(transaction)
        
        self._save_balance()
        
        log.info(
            f"[PointsManager] Consumption: {actual_tokens:,} tokens Ã- {multiplier}x = "
            f"{points_cost:,} points. Remaining: {self._balance.balance:,}"
        )
        
        return {
            "points_consumed": points_cost,
            "tokens_used": actual_tokens,
            "multiplier": multiplier,
            "remaining_balance": self._balance.balance,
            "remaining_tokens": self._balance.balance * 1000,
        }
    
    def get_transaction_history(self, limit: int = 50) -> List[PointsTransaction]:
        """Get recent transaction history."""
        return self._balance.transactions[-limit:]
    
    def get_usage_summary(self) -> dict:
        """Get comprehensive usage summary."""
        return {
            "balance": self._balance.balance,
            "tokens_equivalent": self._balance.balance * 1000,
            "total_purchased": self._balance.total_purchased,
            "total_consumed": self._balance.total_consumed,
            "remaining_percentage": (
                (self._balance.balance / self._balance.total_purchased * 100)
                if self._balance.total_purchased > 0
                else 0
            ),
            "transaction_count": len(self._balance.transactions),
        }
    
    def reset_balance(self, new_balance: int = 0):
        """Reset balance (for testing or admin purposes)."""
        old_balance = self._balance.balance
        self._balance.balance = new_balance
        
        transaction = PointsTransaction(
            transaction_id=f"reset_{int(time.time())}",
            timestamp=time.time(),
            transaction_type="reset",
            points=new_balance - old_balance,
            description=f"Balance reset from {old_balance:,} to {new_balance:,}",
        )
        self._balance.transactions.append(transaction)
        
        self._save_balance()
        log.info(f"[PointsManager] Balance reset: {old_balance:,} → {new_balance:,}")


class InsufficientPointsError(Exception):
    """Raised when user has insufficient points for a request."""
    
    def __init__(self, message: str, required: int, balance: int):
        super().__init__(message)
        self.required = required
        self.balance = balance


# Singleton instance
_points_manager = None


def get_points_manager() -> PointsManager:
    """Get or create the points manager singleton."""
    global _points_manager
    if _points_manager is None:
        _points_manager = PointsManager()
    return _points_manager


def reset_points_manager():
    """Reset the singleton (for testing)."""
    global _points_manager
    _points_manager = None
