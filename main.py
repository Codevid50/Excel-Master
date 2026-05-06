from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import os
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATABASE = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "academy.db")))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
ADMIN_OWNER_EMAIL = os.getenv("ADMIN_OWNER_EMAIL", "").strip().lower()
ADMIN_ACCESS_KEY = os.getenv("ADMIN_ACCESS_KEY", "").strip()

if not SECRET_KEY:
    if ENVIRONMENT == "production":
        raise RuntimeError("SECRET_KEY must be set in production.")
    SECRET_KEY = "development-only-secret-key"


def parse_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


app = FastAPI(
    title="Excel Academy API",
    description="Backend API for Excel Academy Tuition Center",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)
app.add_middleware(GZipMiddleware, minimum_size=500)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response


def normalize_email(email: str) -> str:
    return email.strip().lower()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def build_user_response(user: sqlite3.Row) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "role": user["role"],
        "phone": user["phone"],
        "created_at": user["created_at"],
        "can_access_admin": is_owner_email(user["email"]),
    }


def configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def db_connection():
    conn = sqlite3.connect(DATABASE, timeout=30)
    configure_connection(conn)
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT DEFAULT 'student',
                phone TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                duration TEXT,
                level TEXT,
                price REAL NOT NULL,
                category TEXT,
                badge TEXT,
                image TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                amount_paid REAL NOT NULL,
                payment_status TEXT DEFAULT 'paid',
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, course_id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (course_id) REFERENCES courses (id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS enrollments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                purchase_id INTEGER,
                status TEXT DEFAULT 'active',
                progress INTEGER DEFAULT 0,
                enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, course_id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (course_id) REFERENCES courses (id),
                FOREIGN KEY (purchase_id) REFERENCES purchases (id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS testimonials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT,
                content TEXT NOT NULL,
                rating INTEGER DEFAULT 5,
                initials TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inquiries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                subject TEXT,
                message TEXT NOT NULL,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_purchases_user_id ON purchases (user_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_enrollments_user_id ON enrollments (user_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_inquiries_created_at ON inquiries (created_at DESC)"
        )

        cursor.execute("SELECT COUNT(*) FROM courses")
        if cursor.fetchone()[0] == 0:
            sample_courses = [
                (
                    "Excel Fundamentals",
                    "Build confidence with formulas, functions, formatting, and worksheet navigation.",
                    "4 Weeks",
                    "Beginner",
                    49.0,
                    "Core Skills",
                    "Popular",
                    "gradient-1",
                ),
                (
                    "Dashboard Design",
                    "Turn raw spreadsheets into executive-ready dashboards with charts and KPIs.",
                    "6 Weeks",
                    "Intermediate",
                    89.0,
                    "Visualization",
                    "New",
                    "gradient-2",
                ),
                (
                    "Advanced Formulas",
                    "Master lookup logic, nested formulas, dynamic arrays, and error-proof models.",
                    "8 Weeks",
                    "Intermediate",
                    119.0,
                    "Automation",
                    "Hot",
                    "gradient-3",
                ),
                (
                    "Financial Modeling",
                    "Build forecasting sheets and reusable finance models used in real teams.",
                    "10 Weeks",
                    "Advanced",
                    149.0,
                    "Finance",
                    "",
                    "gradient-4",
                ),
                (
                    "Excel for Data Analysis",
                    "Clean, analyze, and summarize data with pivot tables, Power Query, and reporting workflows.",
                    "8 Weeks",
                    "Advanced",
                    129.0,
                    "Analytics",
                    "",
                    "gradient-5",
                ),
                (
                    "Complete Excel Career Track",
                    "An end-to-end learning path for professionals who want production-ready spreadsheet skills.",
                    "16 Weeks",
                    "All Levels",
                    299.0,
                    "Bundle",
                    "Best Value",
                    "gradient-6",
                ),
            ]
            cursor.executemany(
                """
                INSERT INTO courses (title, description, duration, level, price, category, badge, image)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                sample_courses,
            )

        cursor.execute("SELECT COUNT(*) FROM testimonials")
        if cursor.fetchone()[0] == 0:
            sample_testimonials = [
                (
                    "Sarah Mitchell",
                    "Operations Analyst",
                    "The dashboard module saved me hours every week. I now run reporting without touching external tools.",
                    5,
                    "SM",
                ),
                (
                    "James Rodriguez",
                    "Finance Associate",
                    "The financial modeling track was practical and immediately useful at work.",
                    5,
                    "JR",
                ),
                (
                    "Alex Kim",
                    "Business Student",
                    "I went from basic formulas to building full Excel projects that I could show in interviews.",
                    5,
                    "AK",
                ),
            ]
            cursor.executemany(
                """
                INSERT INTO testimonials (name, role, content, rating, initials)
                VALUES (?, ?, ?, ?, ?)
                """,
                sample_testimonials,
            )

        conn.commit()


@app.on_event("startup")
async def startup() -> None:
    init_db()


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str = Field(..., min_length=2, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    phone: Optional[str]
    created_at: str
    can_access_admin: bool = False


class CourseResponse(BaseModel):
    id: int
    title: str
    description: str
    duration: str
    level: str
    price: float
    category: str
    badge: Optional[str]
    image: Optional[str]
    is_active: bool


class EnrollmentCreate(BaseModel):
    course_id: int = Field(..., gt=0)


class ProgressUpdate(BaseModel):
    progress: int = Field(..., ge=0, le=100)


class EnrollmentResponse(BaseModel):
    id: int
    user_id: int
    course_id: int
    status: str
    progress: int
    enrolled_at: str
    course_title: Optional[str] = None


class PurchaseResponse(EnrollmentResponse):
    purchase_id: int
    amount_paid: float
    payment_status: str
    purchased_at: str


class InquiryCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    phone: Optional[str] = Field(default=None, max_length=30)
    subject: Optional[str] = Field(default=None, max_length=150)
    message: str = Field(..., min_length=10, max_length=2000)


class InquiryResponse(BaseModel):
    id: int
    name: str
    email: str
    phone: Optional[str]
    subject: Optional[str]
    message: str
    status: str
    created_at: str


class TestimonialCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    role: str = Field(..., min_length=2, max_length=120)
    content: str = Field(..., min_length=10, max_length=1000)
    rating: int = Field(default=5, ge=1, le=5)


class TestimonialResponse(BaseModel):
    id: int
    name: str
    role: str
    content: str
    rating: int
    initials: str


class Token(BaseModel):
    access_token: str
    token_type: str


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode = data.copy()
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def is_owner_email(email: Optional[str]) -> bool:
    return bool(ADMIN_OWNER_EMAIL and email and normalize_email(email) == ADMIN_OWNER_EMAIL)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return int(user_id)
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_admin(
    user_id: int = Depends(get_current_user),
    x_admin_key: Optional[str] = Header(default=None),
) -> int:
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, email FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not ADMIN_OWNER_EMAIL or not ADMIN_ACCESS_KEY:
        raise HTTPException(status_code=503, detail="Admin access is not configured")
    if not is_owner_email(user["email"]):
        raise HTTPException(status_code=403, detail="Owner access required")
    if x_admin_key != ADMIN_ACCESS_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin access key")
    return user_id


def build_purchase_response(row: sqlite3.Row) -> dict:
    return {
        "id": row["enrollment_id"],
        "user_id": row["user_id"],
        "course_id": row["course_id"],
        "status": row["status"],
        "progress": row["progress"],
        "enrolled_at": row["enrolled_at"],
        "course_title": row["course_title"],
        "purchase_id": row["purchase_id"],
        "amount_paid": row["amount_paid"],
        "payment_status": row["payment_status"],
        "purchased_at": row["purchased_at"],
    }


def fetch_user_purchases(user_id: int) -> list[dict]:
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                e.id AS enrollment_id,
                e.user_id,
                e.course_id,
                e.status,
                e.progress,
                e.enrolled_at,
                c.title AS course_title,
                p.id AS purchase_id,
                p.amount_paid,
                p.payment_status,
                p.purchased_at
            FROM purchases p
            JOIN courses c ON c.id = p.course_id
            LEFT JOIN enrollments e ON e.purchase_id = p.id
            WHERE p.user_id = ?
            ORDER BY p.purchased_at DESC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
    return [build_purchase_response(row) for row in rows]


@app.get("/", response_class=FileResponse)
def serve_index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health() -> dict:
    return {
        "message": "Excel Academy API",
        "version": "1.1.0",
        "status": "running",
        "environment": ENVIRONMENT,
    }


@app.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user: UserCreate):
    email = normalize_email(str(user.email))
    with db_connection() as conn:
        cursor = conn.cursor()
        hashed_password = get_password_hash(user.password)
        try:
            cursor.execute(
                """
                INSERT INTO users (email, password, full_name, phone)
                VALUES (?, ?, ?, ?)
                """,
                (email, hashed_password, user.full_name.strip(), user.phone.strip() if user.phone else None),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Email already registered")

        user_id = cursor.lastrowid
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        db_user = cursor.fetchone()

    return build_user_response(db_user)


@app.post("/auth/login", response_model=Token)
def login(user: UserLogin):
    email = normalize_email(str(user.email))
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        db_user = cursor.fetchone()

    if not db_user or not verify_password(user.password, db_user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_access_token(
        data={"sub": str(db_user["id"]), "email": db_user["email"], "role": db_user["role"]},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/auth/me", response_model=UserResponse)
def get_me(user_id: int = Depends(get_current_user)):
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        db_user = cursor.fetchone()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return build_user_response(db_user)


@app.get("/courses", response_model=list[CourseResponse])
def get_courses(category: Optional[str] = None, level: Optional[str] = None):
    with db_connection() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM courses WHERE is_active = 1"
        params: list[str] = []

        if category:
            query += " AND category = ?"
            params.append(category.strip())
        if level:
            query += " AND level = ?"
            params.append(level.strip())

        query += " ORDER BY created_at DESC"
        cursor.execute(query, params)
        courses = cursor.fetchall()

    return [row_to_dict(course) for course in courses]


@app.get("/courses/{course_id}", response_model=CourseResponse)
def get_course(course_id: int):
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM courses WHERE id = ? AND is_active = 1", (course_id,))
        course = cursor.fetchone()

    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return row_to_dict(course)


@app.post("/purchases", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED)
def create_purchase(enrollment: EnrollmentCreate, user_id: int = Depends(get_current_user)):
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, price FROM courses WHERE id = ? AND is_active = 1",
            (enrollment.course_id,),
        )
        course = cursor.fetchone()
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        try:
            cursor.execute(
                """
                INSERT INTO purchases (user_id, course_id, amount_paid, payment_status)
                VALUES (?, ?, ?, 'paid')
                """,
                (user_id, enrollment.course_id, course["price"]),
            )
            purchase_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO enrollments (user_id, course_id, purchase_id, status, progress)
                VALUES (?, ?, ?, 'active', 0)
                """,
                (user_id, enrollment.course_id, purchase_id),
            )
            enrollment_id = cursor.lastrowid
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Course already purchased")

        cursor.execute(
            """
            SELECT
                e.id AS enrollment_id,
                e.user_id,
                e.course_id,
                e.status,
                e.progress,
                e.enrolled_at,
                c.title AS course_title,
                p.id AS purchase_id,
                p.amount_paid,
                p.payment_status,
                p.purchased_at
            FROM enrollments e
            JOIN purchases p ON p.id = e.purchase_id
            JOIN courses c ON c.id = e.course_id
            WHERE e.id = ? AND p.id = ?
            """,
            (enrollment_id, purchase_id),
        )
        created = cursor.fetchone()

    return build_purchase_response(created)


@app.get("/purchases", response_model=list[PurchaseResponse])
def get_my_purchases(user_id: int = Depends(get_current_user)):
    return fetch_user_purchases(user_id)


@app.get("/admin/data")
def get_admin_data(admin_user_id: int = Depends(get_current_admin)):
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, email, full_name, role, phone, created_at
            FROM users
            ORDER BY created_at DESC
            """
        )
        users = [row_to_dict(row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT
                p.id,
                p.user_id,
                u.full_name,
                u.email,
                p.course_id,
                c.title AS course_title,
                p.amount_paid,
                p.payment_status,
                p.purchased_at
            FROM purchases p
            JOIN users u ON u.id = p.user_id
            JOIN courses c ON c.id = p.course_id
            ORDER BY p.purchased_at DESC
            """
        )
        purchases = [row_to_dict(row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT
                e.id,
                e.user_id,
                u.full_name,
                u.email,
                e.course_id,
                c.title AS course_title,
                e.status,
                e.progress,
                e.enrolled_at
            FROM enrollments e
            JOIN users u ON u.id = e.user_id
            JOIN courses c ON c.id = e.course_id
            ORDER BY e.enrolled_at DESC
            """
        )
        enrollments = [row_to_dict(row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT id, name, email, phone, subject, message, status, created_at
            FROM inquiries
            ORDER BY created_at DESC
            """
        )
        inquiries = [row_to_dict(row) for row in cursor.fetchall()]

    return {
        "viewer_id": admin_user_id,
        "counts": {
            "users": len(users),
            "purchases": len(purchases),
            "enrollments": len(enrollments),
            "inquiries": len(inquiries),
        },
        "users": users,
        "purchases": purchases,
        "enrollments": enrollments,
        "inquiries": inquiries,
    }


@app.post("/enrollments", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED)
def create_enrollment(enrollment: EnrollmentCreate, user_id: int = Depends(get_current_user)):
    return create_purchase(enrollment, user_id)


@app.get("/enrollments", response_model=list[PurchaseResponse])
def get_my_enrollments(user_id: int = Depends(get_current_user)):
    return fetch_user_purchases(user_id)


@app.get("/enrollments/me", response_model=list[PurchaseResponse])
def get_my_enrollments_alias(user_id: int = Depends(get_current_user)):
    return fetch_user_purchases(user_id)


@app.patch("/enrollments/{enrollment_id}/progress")
def update_progress(
    enrollment_id: int,
    payload: ProgressUpdate,
    user_id: int = Depends(get_current_user),
):
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM enrollments WHERE id = ?", (enrollment_id,))
        enrollment = cursor.fetchone()

        if not enrollment or enrollment["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        cursor.execute(
            "UPDATE enrollments SET progress = ? WHERE id = ?",
            (payload.progress, enrollment_id),
        )
        conn.commit()

    return {"message": "Progress updated", "progress": payload.progress}


@app.post("/inquiries", response_model=InquiryResponse, status_code=status.HTTP_201_CREATED)
def create_inquiry(inquiry: InquiryCreate):
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO inquiries (name, email, phone, subject, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                inquiry.name.strip(),
                normalize_email(str(inquiry.email)),
                inquiry.phone.strip() if inquiry.phone else None,
                inquiry.subject.strip() if inquiry.subject else None,
                inquiry.message.strip(),
            ),
        )
        inquiry_id = cursor.lastrowid
        conn.commit()
        cursor.execute("SELECT * FROM inquiries WHERE id = ?", (inquiry_id,))
        db_inquiry = cursor.fetchone()

    return row_to_dict(db_inquiry)


@app.get("/inquiries", response_model=list[InquiryResponse])
def get_inquiries(admin_user_id: int = Depends(get_current_admin)):
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM inquiries ORDER BY created_at DESC")
        inquiries = cursor.fetchall()
    return [row_to_dict(inquiry) for inquiry in inquiries]


@app.get("/testimonials", response_model=list[TestimonialResponse])
def get_testimonials(limit: int = 10):
    if limit < 1 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM testimonials ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        testimonials = cursor.fetchall()
    return [row_to_dict(testimonial) for testimonial in testimonials]


@app.post("/testimonials", response_model=TestimonialResponse, status_code=status.HTTP_201_CREATED)
def create_testimonial(
    testimonial: TestimonialCreate,
    user_id: int = Depends(get_current_user),
):
    del user_id
    initials = "".join(part[0].upper() for part in testimonial.name.split()[:2])

    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO testimonials (name, role, content, rating, initials)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                testimonial.name.strip(),
                testimonial.role.strip(),
                testimonial.content.strip(),
                testimonial.rating,
                initials,
            ),
        )
        testimonial_id = cursor.lastrowid
        conn.commit()
        cursor.execute("SELECT * FROM testimonials WHERE id = ?", (testimonial_id,))
        created = cursor.fetchone()

    return row_to_dict(created)


@app.get("/stats")
def get_stats():
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        students = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM courses WHERE is_active = 1")
        courses = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM purchases WHERE payment_status = 'paid'")
        purchases = cursor.fetchone()[0]
        cursor.execute("SELECT AVG(rating) FROM testimonials")
        avg_rating = cursor.fetchone()[0] or 5.0

    return {
        "students": students + 5000,
        "tutors": 24,
        "courses": courses,
        "satisfaction": min(99, int(avg_rating * 20)),
        "purchases": purchases,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
