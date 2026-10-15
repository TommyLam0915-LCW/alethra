"""IBKR API client wrapper using ib_insync.

This module wraps the `ib_insync` library to provide a clean interface for:
- Connecting to IB Gateway / TWS
- Resolving instrument_id to IBKR Contract
- Submitting orders (market, limit)
- Tracking fills and order status
- Handling reconnection and errors

STATUS: NOT IMPLEMENTED — skeleton for architecture documentation.

Requires: pip install ib_insync
"""

from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime


@dataclass
class IBKROrderResult:
    """Result of an order submission to IBKR.
    
    Attributes:
        success: Whether the order was successfully submitted
        ibkr_order_id: IBKR-assigned order ID (if successful)
        error_message: Error message (if failed)
        submitted_at: Timestamp of submission
    """
    success: bool
    ibkr_order_id: Optional[int] = None
    error_message: Optional[str] = None
    submitted_at: Optional[datetime] = None


@dataclass
class IBKRFill:
    """A fill event from IBKR.
    
    Attributes:
        ibkr_order_id: IBKR order ID
        quantity: Filled quantity
        price: Fill price
        fill_time: Timestamp of fill
        commission: Commission charged
    """
    ibkr_order_id: int
    quantity: int
    price: float
    fill_time: datetime
    commission: float


class IBKRClient:
    """IBKR API client wrapper.
    
    This class manages the connection to IB Gateway/TWS and provides methods
    for order submission and fill tracking.
    
    STATUS: NOT IMPLEMENTED
    """
    
    def __init__(self, host: str, port: int, client_id: int):
        """Initialize IBKR client.
        
        Args:
            host: IB Gateway host (typically "localhost")
            port: IB Gateway API port (7496 for live, 7497 for paper)
            client_id: IBKR client ID (must be unique per connection)
        """
        self.host = host
        self.port = port
        self.client_id = client_id
        self.connected = False
        # self.ib = None  # ib_insync.IB() instance
        raise NotImplementedError(
            "IBKRClient is not implemented. "
            "Requires IBKR account and IB Gateway installation. "
            "See docs/external-prerequisites-checklist.md"
        )
    
    def connect(self) -> None:
        """Connect to IB Gateway/TWS.
        
        Raises:
            ConnectionError: If connection fails
        """
        # Implementation:
        # self.ib = ib_insync.IB()
        # self.ib.connect(self.host, self.port, clientId=self.client_id)
        # self.connected = True
        raise NotImplementedError
    
    def disconnect(self) -> None:
        """Disconnect from IB Gateway/TWS."""
        # if self.ib:
        #     self.ib.disconnect()
        # self.connected = False
        raise NotImplementedError
    
    def is_connected(self) -> bool:
        """Check if connected to IB Gateway/TWS."""
        return self.connected
    
    def resolve_contract(self, instrument_id: str) -> object:
        """Resolve instrument_id to IBKR Contract.
        
        Args:
            instrument_id: Alethra instrument ID (e.g., "US:AAPL", "HK:0700")
        
        Returns:
            ib_insync.Contract object
        
        Raises:
            ValueError: If instrument cannot be resolved
        """
        # Implementation:
        # Parse instrument_id → (market, symbol)
        # Map to IBKR Contract:
        #   US:AAPL → Contract("AAPL", "STK", "SMART", "USD", primaryExch="NASDAQ")
        #   HK:0700 → Contract("0700", "STK", "SEHK", "HKD")
        # Use ib_insync.IB.qualifyContracts() to resolve
        raise NotImplementedError
    
    def submit_market_order(
        self,
        instrument_id: str,
        side: str,  # "BUY" or "SELL"
        quantity: int,
    ) -> IBKROrderResult:
        """Submit a market order to IBKR.
        
        Args:
            instrument_id: Alethra instrument ID
            side: "BUY" or "SELL"
            quantity: Order quantity
        
        Returns:
            IBKROrderResult with submission status
        """
        # Implementation:
        # contract = self.resolve_contract(instrument_id)
        # order = ib_insync.MarketOrder(side, quantity)
        # trade = self.ib.placeOrder(contract, order)
        # return IBKROrderResult(success=True, ibkr_order_id=trade.order.orderId, ...)
        raise NotImplementedError
    
    def submit_limit_order(
        self,
        instrument_id: str,
        side: str,  # "BUY" or "SELL"
        quantity: int,
        limit_price: float,
    ) -> IBKROrderResult:
        """Submit a limit order to IBKR.
        
        Args:
            instrument_id: Alethra instrument ID
            side: "BUY" or "SELL"
            quantity: Order quantity
            limit_price: Limit price
        
        Returns:
            IBKROrderResult with submission status
        """
        # Implementation:
        # contract = self.resolve_contract(instrument_id)
        # order = ib_insync.LimitOrder(side, quantity, limit_price)
        # trade = self.ib.placeOrder(contract, order)
        # return IBKROrderResult(success=True, ibkr_order_id=trade.order.orderId, ...)
        raise NotImplementedError
    
    def cancel_order(self, ibkr_order_id: int) -> bool:
        """Cancel an order.
        
        Args:
            ibkr_order_id: IBKR order ID
        
        Returns:
            True if cancellation was successful
        """
        # Implementation:
        # Find trade by order ID
        # self.ib.cancelOrder(trade)
        raise NotImplementedError
    
    def get_fills(self, ibkr_order_id: int) -> List[IBKRFill]:
        """Get fills for an order.
        
        Args:
            ibkr_order_id: IBKR order ID
        
        Returns:
            List of IBKRFill objects
        """
        # Implementation:
        # Query self.ib.fills() for matching order ID
        # Convert to IBKRFill objects
        raise NotImplementedError
    
    def get_order_status(self, ibkr_order_id: int) -> str:
        """Get order status.
        
        Args:
            ibkr_order_id: IBKR order ID
        
        Returns:
            Status string: "Submitted", "Filled", "Cancelled", "Rejected", etc.
        """
        # Implementation:
        # Find trade by order ID
        # return trade.orderStatus.status
        raise NotImplementedError
