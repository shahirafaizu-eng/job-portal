# RoyalJobs — Mass & Royal Job Portal

A full-stack job portal built with **Python (Flask)**, **SQLite**, **HTML**, **CSS**, and **JavaScript**.

## Features
- Job seeker & employer registration/login (secure password hashing)
- Employers: post, edit, delete jobs; view & manage applicants (shortlist/reject/hire)
- Job seekers: search/filter jobs, apply with resume upload + cover letter, save jobs, track application status
- Responsive royal-themed UI (navy & gold)
- SQLite database, auto-created on first run

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** in your browser. The database (`jobportal.db`) and tables are created automatically on first run.

## Project Structure
```
jobportal/
├── app.py                 # Flask app: routes, auth, DB logic
├── requirements.txt
├── jobportal.db            # SQLite database (auto-created)
├── uploads/                 # Uploaded resumes
├── templates/                # Jinja2 HTML templates
│   ├── base.html, index.html, jobs.html, job_detail.html
│   ├── login.html, register.html, profile.html
│   ├── dashboard_employer.html, dashboard_seeker.html
│   ├── post_job.html, apply_job.html, applicants.html, error.html
└── static/
    ├── css/style.css        # Royal navy & gold theme
    └── js/script.js         # Mobile nav + UX helpers
```

## Database Schema (SQLite)
- **users** — id, name, email, password_hash, role (seeker/employer/admin), company_name, phone, bio
- **jobs** — id, employer_id, title, company, location, job_type, category, salary range, description, requirements, status
- **applications** — id, job_id, seeker_id, resume_path, cover_letter, status
- **saved_jobs** — id, seeker_id, job_id

## Notes
- Change `app.config["SECRET_KEY"]` in `app.py` before deploying to production.
- Resumes are limited to PDF/DOC/DOCX, max 5MB.
- To switch to MySQL/PostgreSQL later, swap the `sqlite3` calls in `app.py` for `SQLAlchemy` models — the schema maps directly.
