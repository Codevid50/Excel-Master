# ExcelMaster Tutoring Website

ExcelMaster is a full-stack tutoring website built with FastAPI, SQLite, and a single-page HTML/CSS/JavaScript frontend. It includes user registration and login, course browsing, purchases and enrollments, progress tracking, testimonials, contact inquiries, and an owner-only admin dashboard for managing platform activity.

## Features

- FastAPI backend with JWT-based authentication
- Responsive landing page with animated UI
- Course listing and purchase flow
- Student enrollment and progress tracking
- Testimonials and inquiry endpoints
- Owner-only admin dashboard with protected access
- SQLite database for simple deployment
- Ready for Render deployment

## Tech Stack

- Backend: FastAPI
- Frontend: HTML, CSS, JavaScript
- Database: SQLite
- Auth: JWT
- Server: Uvicorn

## Project Structure

```text
.
|-- main.py
|-- index.html
|-- requirements.txt
|-- academy.db
|-- .env.example
`-- README.md
```

## Local Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Set environment variables or create a local `.env` equivalent in your shell:

```env
ENVIRONMENT=development
SECRET_KEY=your-local-secret
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ADMIN_OWNER_EMAIL=you@example.com
ADMIN_ACCESS_KEY=your-admin-key
CORS_ORIGINS=http://localhost:8000
DATABASE_PATH=academy.db
```

4. Run the app:

```bash
uvicorn main:app --reload
```

5. Open:

```text
http://localhost:8000
```

## Production Environment Variables

Use these on Render or any hosting provider:

```env
ENVIRONMENT=production
SECRET_KEY=replace-with-a-long-random-secret
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ADMIN_OWNER_EMAIL=owner@example.com
ADMIN_ACCESS_KEY=replace-with-a-second-secret
CORS_ORIGINS=https://your-service-name.onrender.com
DATABASE_PATH=academy.db
```

## Deploy on Render

1. Push the project to GitHub.
2. Create a new Web Service on Render.
3. Select the repository.
4. Use these settings:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
```

5. Add the required environment variables in Render.
6. If you want your SQLite data to persist across deploys, attach a persistent disk and point `DATABASE_PATH` to that mounted location.

## Notes

- The default `academy.db` is suitable for demos and small deployments.
- For larger production use, PostgreSQL is a better long-term choice.
- The frontend uses the same origin by default in production, which keeps deployment simple on Render.

## License

This project is provided as a sellable website/codebase. Add your own license or transfer terms before selling it publicly.
