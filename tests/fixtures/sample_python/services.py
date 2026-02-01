"""Business logic services."""

from models import User, Order


class UserService:
    """Service for user management operations."""

    def __init__(self, db):
        self.db = db

    def get_user(self, user_id: int) -> User:
        """Fetch a user by their ID."""
        return self.db.find(User, user_id)

    def create_user(self, name: str, email: str) -> User:
        """Create and persist a new user."""
        if not validate_email(email):
            raise ValueError(f"Invalid email: {email}")
        user = User(id=0, name=name, email=email)
        self.db.save(user)
        return user

    async def deactivate_user(self, user_id: int) -> None:
        """Mark a user as inactive."""
        user = self.get_user(user_id)
        user.is_active = False
        self.db.save(user)


class OrderService:
    """Service for order management."""

    def __init__(self, db, user_service: UserService):
        self.db = db
        self.user_service = user_service

    def create_order(self, user_id: int, total: float) -> Order:
        """Create a new order for a user."""
        user = self.user_service.get_user(user_id)
        if not user.is_active:
            raise ValueError("Cannot create order for inactive user")
        order = Order(id=0, user_id=user_id, total=total)
        self.db.save(order)
        notify_order_created(order)
        return order

    def get_user_orders(self, user_id: int) -> list:
        """Get all orders for a specific user."""
        return self.db.query(Order, user_id=user_id)


def validate_email(email: str) -> bool:
    """Validate that an email address is properly formatted."""
    return "@" in email and "." in email.split("@")[-1]


def notify_order_created(order: Order) -> None:
    """Send notification when an order is created."""
    print(f"Order {order.id} created for user {order.user_id}")
