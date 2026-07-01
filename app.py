"""
Royal Job Portal — Flask + SQLite backend
==========================================
A full job portal: employers post jobs, job seekers apply.
Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000
"""

import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, abort, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "jobportal.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
ALLOWED_RESUME_EXT = {"pdf", "doc", "docx"}

app = Flask(__name__)
app.config["SECRET_KEY"] = "royal-job-portal-secret-key-change-me"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ----------------------------------------------------------------------
# Database helpers
# ----------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('seeker','employer','admin')),
            company_name TEXT,
            phone TEXT,
            bio TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employer_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT NOT NULL,
            job_type TEXT NOT NULL,
            category TEXT,
            salary_min INTEGER,
            salary_max INTEGER,
            description TEXT NOT NULL,
            requirements TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','closed')),
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (employer_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            seeker_id INTEGER NOT NULL,
            resume_path TEXT,
            cover_letter TEXT,
            status TEXT NOT NULL DEFAULT 'applied' CHECK(status IN ('applied','shortlisted','rejected','hired')),
            applied_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
            FOREIGN KEY (seeker_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(job_id, seeker_id)
        );

        CREATE TABLE IF NOT EXISTS saved_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seeker_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            saved_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (seeker_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
            UNIQUE(seeker_id, job_id)
        );
        """
    )
    db.commit()
    db.close()


# ----------------------------------------------------------------------
# Auth helpers
# ----------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if session.get("role") != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def current_user():
    if "user_id" not in session:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


def allowed_resume(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXT


# ----------------------------------------------------------------------
# Public routes
# ----------------------------------------------------------------------

@app.route("/")
def index():
    db = get_db()
    latest_jobs = db.execute(
        "SELECT * FROM jobs WHERE status='active' ORDER BY created_at DESC LIMIT 6"
    ).fetchall()
    total_jobs = db.execute("SELECT COUNT(*) c FROM jobs WHERE status='active'").fetchone()["c"]
    total_companies = db.execute(
        "SELECT COUNT(DISTINCT employer_id) c FROM jobs WHERE status='active'"
    ).fetchone()["c"]
    total_seekers = db.execute("SELECT COUNT(*) c FROM users WHERE role='seeker'").fetchone()["c"]
    return render_template(
        "index.html",
        jobs=latest_jobs,
        total_jobs=total_jobs,
        total_companies=total_companies,
        total_seekers=total_seekers,
    )


@app.route("/jobs")
def jobs():
    db = get_db()
    q = request.args.get("q", "").strip()
    location = request.args.get("location", "").strip()
    job_type = request.args.get("job_type", "").strip()
    category = request.args.get("category", "").strip()

    sql = "SELECT * FROM jobs WHERE status='active'"
    params = []
    if q:
        sql += " AND (title LIKE ? OR company LIKE ? OR description LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like]
    if location:
        sql += " AND location LIKE ?"
        params.append(f"%{location}%")
    if job_type:
        sql += " AND job_type = ?"
        params.append(job_type)
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY created_at DESC"

    job_list = db.execute(sql, params).fetchall()
    categories = db.execute(
        "SELECT DISTINCT category FROM jobs WHERE category IS NOT NULL AND category != ''"
    ).fetchall()
    return render_template(
        "jobs.html", jobs=job_list, q=q, location=location,
        job_type=job_type, category=category, categories=categories,
    )


@app.route("/jobs/<int:job_id>")
def job_detail(job_id):
    db = get_db()
    job = db.execute(
        "SELECT jobs.*, users.email AS employer_email, users.phone AS employer_phone "
        "FROM jobs JOIN users ON jobs.employer_id = users.id WHERE jobs.id = ?",
        (job_id,),
    ).fetchone()
    if job is None:
        abort(404)

    already_applied = False
    already_saved = False
    user = current_user()
    if user and user["role"] == "seeker":
        already_applied = db.execute(
            "SELECT 1 FROM applications WHERE job_id=? AND seeker_id=?",
            (job_id, user["id"]),
        ).fetchone() is not None
        already_saved = db.execute(
            "SELECT 1 FROM saved_jobs WHERE job_id=? AND seeker_id=?",
            (job_id, user["id"]),
        ).fetchone() is not None

    return render_template(
        "job_detail.html", job=job,
        already_applied=already_applied, already_saved=already_saved,
    )


# ----------------------------------------------------------------------
# Auth routes
# ----------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        role = request.form["role"]
        company_name = request.form.get("company_name", "").strip()

        if not name or not email or not password or role not in ("seeker", "employer"):
            flash("Please fill all required fields correctly.", "danger")
            return redirect(url_for("register"))

        db = get_db()
        existing = db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            flash("An account with this email already exists.", "danger")
            return redirect(url_for("register"))

        db.execute(
            "INSERT INTO users (name, email, password_hash, role, company_name) VALUES (?,?,?,?,?)",
            (name, email, generate_password_hash(password), role, company_name or None),
        )
        db.commit()
        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["name"] = user["name"]
            flash(f"Welcome back, {user['name']}!", "success")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    if session["role"] == "employer":
        return redirect(url_for("employer_dashboard"))
    elif session["role"] == "seeker":
        return redirect(url_for("seeker_dashboard"))
    return redirect(url_for("index"))


@app.route("/employer/dashboard")
@role_required("employer")
def employer_dashboard():
    db = get_db()
    employer_id = session["user_id"]
    jobs = db.execute(
        "SELECT * FROM jobs WHERE employer_id=? ORDER BY created_at DESC", (employer_id,)
    ).fetchall()
    job_ids = [j["id"] for j in jobs]
    app_counts = {}
    if job_ids:
        placeholders = ",".join("?" * len(job_ids))
        rows = db.execute(
            f"SELECT job_id, COUNT(*) c FROM applications WHERE job_id IN ({placeholders}) GROUP BY job_id",
            job_ids,
        ).fetchall()
        app_counts = {r["job_id"]: r["c"] for r in rows}
    return render_template("dashboard_employer.html", jobs=jobs, app_counts=app_counts)


@app.route("/seeker/dashboard")
@role_required("seeker")
def seeker_dashboard():
    db = get_db()
    seeker_id = session["user_id"]
    applications = db.execute(
        "SELECT applications.*, jobs.title, jobs.company, jobs.location "
        "FROM applications JOIN jobs ON applications.job_id = jobs.id "
        "WHERE seeker_id=? ORDER BY applied_at DESC",
        (seeker_id,),
    ).fetchall()
    saved = db.execute(
        "SELECT jobs.* FROM saved_jobs JOIN jobs ON saved_jobs.job_id = jobs.id "
        "WHERE seeker_id=? ORDER BY saved_jobs.saved_at DESC",
        (seeker_id,),
    ).fetchall()
    return render_template("dashboard_seeker.html", applications=applications, saved=saved)


# ----------------------------------------------------------------------
# Employer: job management
# ----------------------------------------------------------------------

@app.route("/employer/jobs/new", methods=["GET", "POST"])
@role_required("employer")
def post_job():
    if request.method == "POST":
        db = get_db()
        title = request.form["title"].strip()
        company = request.form["company"].strip()
        location = request.form["location"].strip()
        job_type = request.form["job_type"]
        category = request.form.get("category", "").strip()
        salary_min = request.form.get("salary_min") or None
        salary_max = request.form.get("salary_max") or None
        description = request.form["description"].strip()
        requirements = request.form.get("requirements", "").strip()

        if not title or not company or not location or not description:
            flash("Please fill all required fields.", "danger")
            return redirect(url_for("post_job"))

        db.execute(
            """INSERT INTO jobs
               (employer_id, title, company, location, job_type, category,
                salary_min, salary_max, description, requirements)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (session["user_id"], title, company, location, job_type, category,
             salary_min, salary_max, description, requirements),
        )
        db.commit()
        flash("Job posted successfully!", "success")
        return redirect(url_for("employer_dashboard"))

    return render_template("post_job.html")


@app.route("/employer/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@role_required("employer")
def edit_job(job_id):
    db = get_db()
    job = db.execute(
        "SELECT * FROM jobs WHERE id=? AND employer_id=?", (job_id, session["user_id"])
    ).fetchone()
    if job is None:
        abort(404)

    if request.method == "POST":
        title = request.form["title"].strip()
        company = request.form["company"].strip()
        location = request.form["location"].strip()
        job_type = request.form["job_type"]
        category = request.form.get("category", "").strip()
        salary_min = request.form.get("salary_min") or None
        salary_max = request.form.get("salary_max") or None
        description = request.form["description"].strip()
        requirements = request.form.get("requirements", "").strip()
        status = request.form.get("status", "active")

        db.execute(
            """UPDATE jobs SET title=?, company=?, location=?, job_type=?, category=?,
               salary_min=?, salary_max=?, description=?, requirements=?, status=?
               WHERE id=?""",
            (title, company, location, job_type, category, salary_min, salary_max,
             description, requirements, status, job_id),
        )
        db.commit()
        flash("Job updated successfully.", "success")
        return redirect(url_for("employer_dashboard"))

    return render_template("post_job.html", job=job)


@app.route("/employer/jobs/<int:job_id>/delete", methods=["POST"])
@role_required("employer")
def delete_job(job_id):
    db = get_db()
    job = db.execute(
        "SELECT * FROM jobs WHERE id=? AND employer_id=?", (job_id, session["user_id"])
    ).fetchone()
    if job is None:
        abort(404)
    db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    db.commit()
    flash("Job deleted.", "info")
    return redirect(url_for("employer_dashboard"))


@app.route("/employer/jobs/<int:job_id>/applicants")
@role_required("employer")
def view_applicants(job_id):
    db = get_db()
    job = db.execute(
        "SELECT * FROM jobs WHERE id=? AND employer_id=?", (job_id, session["user_id"])
    ).fetchone()
    if job is None:
        abort(404)
    applicants = db.execute(
        "SELECT applications.*, users.name, users.email, users.phone "
        "FROM applications JOIN users ON applications.seeker_id = users.id "
        "WHERE job_id=? ORDER BY applied_at DESC",
        (job_id,),
    ).fetchall()
    return render_template("applicants.html", job=job, applicants=applicants)


@app.route("/applications/<int:app_id>/status", methods=["POST"])
@role_required("employer")
def update_application_status(app_id):
    db = get_db()
    application = db.execute(
        "SELECT applications.*, jobs.employer_id FROM applications "
        "JOIN jobs ON applications.job_id = jobs.id WHERE applications.id=?",
        (app_id,),
    ).fetchone()
    if application is None or application["employer_id"] != session["user_id"]:
        abort(404)
    new_status = request.form.get("status")
    if new_status not in ("applied", "shortlisted", "rejected", "hired"):
        abort(400)
    db.execute("UPDATE applications SET status=? WHERE id=?", (new_status, app_id))
    db.commit()
    flash("Application status updated.", "success")
    return redirect(url_for("view_applicants", job_id=application["job_id"]))


# ----------------------------------------------------------------------
# Seeker: apply / save jobs
# ----------------------------------------------------------------------

@app.route("/jobs/<int:job_id>/apply", methods=["GET", "POST"])
@role_required("seeker")
def apply_job(job_id):
    db = get_db()
    job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if job is None:
        abort(404)

    existing = db.execute(
        "SELECT 1 FROM applications WHERE job_id=? AND seeker_id=?",
        (job_id, session["user_id"]),
    ).fetchone()
    if existing:
        flash("You have already applied to this job.", "warning")
        return redirect(url_for("job_detail", job_id=job_id))

    if request.method == "POST":
        cover_letter = request.form.get("cover_letter", "").strip()
        resume_path = None

        file = request.files.get("resume")
        if file and file.filename:
            if not allowed_resume(file.filename):
                flash("Resume must be a PDF, DOC, or DOCX file.", "danger")
                return redirect(url_for("apply_job", job_id=job_id))
            filename = secure_filename(
                f"{session['user_id']}_{job_id}_{int(datetime.now().timestamp())}_{file.filename}"
            )
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            resume_path = filename

        db.execute(
            "INSERT INTO applications (job_id, seeker_id, resume_path, cover_letter) VALUES (?,?,?,?)",
            (job_id, session["user_id"], resume_path, cover_letter),
        )
        db.commit()
        flash("Application submitted successfully!", "success")
        return redirect(url_for("seeker_dashboard"))

    return render_template("apply_job.html", job=job)


@app.route("/jobs/<int:job_id>/save", methods=["POST"])
@role_required("seeker")
def save_job(job_id):
    db = get_db()
    try:
        db.execute(
            "INSERT INTO saved_jobs (seeker_id, job_id) VALUES (?,?)",
            (session["user_id"], job_id),
        )
        db.commit()
        flash("Job saved.", "success")
    except sqlite3.IntegrityError:
        flash("Job already saved.", "info")
    return redirect(url_for("job_detail", job_id=job_id))


@app.route("/jobs/<int:job_id>/unsave", methods=["POST"])
@role_required("seeker")
def unsave_job(job_id):
    db = get_db()
    db.execute(
        "DELETE FROM saved_jobs WHERE seeker_id=? AND job_id=?",
        (session["user_id"], job_id),
    )
    db.commit()
    flash("Job removed from saved list.", "info")
    return redirect(request.referrer or url_for("seeker_dashboard"))


@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ----------------------------------------------------------------------
# Profile
# ----------------------------------------------------------------------

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    user = current_user()
    if request.method == "POST":
        name = request.form["name"].strip()
        phone = request.form.get("phone", "").strip()
        bio = request.form.get("bio", "").strip()
        company_name = request.form.get("company_name", "").strip()
        db.execute(
            "UPDATE users SET name=?, phone=?, bio=?, company_name=? WHERE id=?",
            (name, phone, bio, company_name or None, user["id"]),
        )
        db.commit()
        session["name"] = name
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", user=user)


# ----------------------------------------------------------------------
# Error handlers
# ----------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found"), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="Access forbidden"), 403


if __name__ == "__main__":
    if not os.path.exists(DATABASE):
        init_db()
        print("Database initialized.")
    else:
        init_db()  # ensures tables exist even if db file present but empty
    app.run(debug=True)
