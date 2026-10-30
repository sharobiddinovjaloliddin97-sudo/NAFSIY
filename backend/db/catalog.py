"""Register every model without connecting to a database."""
from backend.db.database import Base
from backend.db import (models, auth_models, user_data_models, ai_models,
                        idempotency_models, lease_models, auth_security_models, operations_models)
