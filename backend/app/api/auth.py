from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.auth.jwt import get_password_hash, create_access_token, get_current_user
from app.auth.rate_limit import rate_limit_dependency
from app.auth.schemas import UserCreate, UserResponse, Token, LoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])

# Credential endpoints are the only unauthenticated write surface, so they need
# their own budget: without it, login is an unlimited password oracle. The bucket
# is keyed on the TCP peer (``request.client.host``); a deployment behind a
# reverse proxy would put every user in one bucket until real client addresses
# are plumbed through, which is a known limitation rather than a silent default.
LOGIN_ATTEMPTS_PER_WINDOW = 20
LOGIN_WINDOW_SECONDS = 60
REGISTRATIONS_PER_WINDOW = 5
REGISTRATION_WINDOW_SECONDS = 300


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    _rate_limit=Depends(
        rate_limit_dependency(
            REGISTRATIONS_PER_WINDOW,
            REGISTRATION_WINDOW_SECONDS,
            bucket="auth_register",
        )
    ),
):
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    user = User(
        email=user_data.email,
        password_hash=get_password_hash(user_data.password),
        display_name=user_data.display_name,
        user_type=user_data.user_type
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(
    credentials: LoginRequest,
    db: Session = Depends(get_db),
    _rate_limit=Depends(
        rate_limit_dependency(
            LOGIN_ATTEMPTS_PER_WINDOW,
            LOGIN_WINDOW_SECONDS,
            bucket="auth_login",
        )
    ),
):
    user = db.query(User).filter(User.email == credentials.email).first()
    if not user or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )

    from app.auth.jwt import verify_password
    if not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )

    # ``amr`` records that this session began with a password check. Provenance
    # labelling uses it to separate password sessions from API-key agents; it is
    # not a claim that a person is present for every later request.
    access_token = create_access_token(data={"sub": str(user.id), "amr": ["pwd"]})
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
