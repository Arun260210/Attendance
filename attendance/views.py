# attendance/views.py
from __future__ import annotations

import calendar
import csv
import random
from datetime import date, timedelta
from .forms import SignupForm

import pandas as pd
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q, Exists, OuterRef
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .models import Attendance, Holiday, AttendanceSetting

User = get_user_model()

# Motivational fortunes (shown on dashboard)
FORTUNES = [
    "🌟 Hard work pays off — keep going, success is near!",
    "💡 Innovation is seeing what everyone has seen, and thinking what no one has thought.",
    "🚀 Your potential is endless, take one bold step today.",
    "🌱 Growth starts when you push beyond your comfort zone.",
    "🎯 Focus on progress, not perfection.",
    "🔥 Passion + consistency = unstoppable success.",
    "📚 Every small lesson adds up to big wisdom.",
    "🤝 Teamwork divides the task and multiplies the success.",
    "⚡ Your energy introduces you before you even speak.",
    "✨ Excellence is built on daily habits, not random acts.",
    "🌍 Be the reason someone smiles at work today.",
    "🧩 Challenges are puzzles waiting for your solution.",
    "🌸 A calm mind brings creative ideas.",
    "🏆 Winners keep trying until they succeed.",
    "🕒 Time is your greatest investment — spend it wisely.",
    "🔑 Communication is the bridge to success.",
    "🌄 Every sunrise is a new chance to grow.",
    "🌟 Believe in your ability to make a difference.",
    "🚀 Your dedication inspires others more than you realize.",
    "💪 Strength grows in moments you think you can’t go on, but you do.",
    "🎉 Celebrate small wins — they fuel big victories.",
    "💡 Great ideas often come in simple moments.",
    "🌈 Optimism is a force multiplier.",
    "🔥 Don’t wait for opportunity, create it.",
    "🎯 Focus and persistence always beat raw talent.",
    "🌍 A positive attitude is contagious — spread it.",
    "🤝 Respect and trust are the foundation of strong teams.",
    "✨ Keep learning, because knowledge compounds.",
    "📚 Success is built on preparation meeting opportunity.",
    "🌱 Small steps daily grow into giant leaps.",
    "💡 Your fresh perspective is valuable — share it.",
    "🏆 Every effort today makes tomorrow easier.",
    "🚀 Consistency is the secret sauce to greatness.",
    "🌄 Keep your vision clear, and the path will appear.",
    "🎯 The best way to predict the future is to create it.",
]

# =========================
# Role helpers
# =========================
def is_admin(user) -> bool:
    return user.is_superuser or user.groups.filter(name="Admin").exists()

def is_hr(user) -> bool:
    return user.groups.filter(name="HR").exists()

def is_admin_or_hr(user) -> bool:
    return is_admin(user) or is_hr(user)

def display_name(user: User) -> str:
    full = f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip()
    return full or user.username or user.email

# --- ADDED: link orphan attendance rows to a user on login/signup
def link_attendance_to_user(user):
    email = (user.email or "").strip().lower()
    if not email:
        return
    Attendance.objects.filter(employee__isnull=True, employee_email__iexact=email).update(employee=user)

# =========================
# Date helpers
# =========================
def month_bounds(y: int, m: int) -> tuple[date, date]:
    first = date(y, m, 1)
    last = date(y, m, calendar.monthrange(y, m)[1])
    return first, last

def iter_month_grid(y: int, m: int):
    """Returns a flat list of dates to cover the whole month grid (leading/trailing days included)."""
    cal = calendar.Calendar(firstweekday=0)  # Monday = 0
    return list(cal.itermonthdates(y, m))

def working_days_in_month(y: int, m: int, holiday_set: set[date]) -> list[date]:
    first, last = month_bounds(y, m)
    cur = first
    days = []
    while cur <= last:
        if cur.weekday() < 5 and cur not in holiday_set:
            days.append(cur)
        cur += timedelta(days=1)
    return days

# =========================
# Auth & index
# =========================
def index(request):
    return redirect("dashboard" if request.user.is_authenticated else "login")

def login_view(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            # --- ADDED: attach past rows (by email) to this user
            link_attendance_to_user(user)
            return redirect("dashboard")
        messages.error(request, "Invalid username or password.")
    return render(request, "login.html")

@login_required
def logout_view(request):
    logout(request)
    return redirect("login")

def signup_view(request):
    """
    Self-service signup without OTP:
    - username, email, password
    - email must be unique (validated in form)
    - creates the account, then returns to login
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            # --- OPTIONAL: pre-link now (we'll also link on login anyway)
            link_attendance_to_user(user)
            messages.success(request, "Account created successfully. Please log in.")
            return redirect("login")
        # fall through to re-render with errors
    else:
        form = SignupForm()

    return render(request, "signup.html", {"form": form})

# =========================
# Dashboard
# =========================
@login_required
def dashboard(request):
    user = request.user
    today = date.today()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    first, last = month_bounds(year, month)
    first_of_month = date(year, month, 1)
    prev_dt = first_of_month - timedelta(days=1)
    next_dt = (first_of_month.replace(day=28) + timedelta(days=10)).replace(day=1)

    holidays_qs = Holiday.objects.filter(date__range=(first, last))
    holiday_set = {h.date for h in holidays_qs}
    holiday_map = {h.date: h for h in holidays_qs}

    cal = calendar.Calendar(firstweekday=6)
    month_dates = list(cal.itermonthdates(year, month))
    weeks = [month_dates[i:i + 7] for i in range(0, len(month_dates), 7)]

    # --- CHANGED: read attendance for this user by EMAIL (not FK) so it also works if rows were uploaded before signup
    user_email = (user.email or "").strip().lower()
    present_dates = set(
        Attendance.objects.filter(
            employee_email__iexact=user_email,
            date__range=(first, last),
            check_in_time__isnull=False,
        ).values_list("date", flat=True)
    )

    # earliest check-in times for tooltips
    checkin_by_date = {}
    present_qs = Attendance.objects.filter(
        employee_email__iexact=user_email, date__range=(first, last), check_in_time__isnull=False
    ).values("date", "check_in_time").order_by("date", "check_in_time")
    for row in present_qs:
        d = row["date"]
        if d not in checkin_by_date:
            checkin_by_date[d] = row["check_in_time"]

    grid, present_count, total_working_days = [], 0, 0
    yesterday = today - timedelta(days=1)

    for week in weeks:
        row = []
        for d in week:
            in_month = (d.month == month)
            cell = {"date": d, "in_month": in_month, "badges": [], "tooltip": ""}

            if in_month:
                is_holiday = d in holiday_set
                is_weekend = d.weekday() >= 5
                is_present = d in present_dates

                if is_holiday and is_present:
                    cell["badges"] = ["Present", "Holiday"]
                    present_count += 1
                elif is_holiday:
                    cell["badges"] = ["Holiday"]
                elif is_weekend:
                    cell["badges"] = []
                else:
                    total_working_days += 1
                    if is_present:
                        cell["badges"] = ["Present"]
                        present_count += 1
                    else:
                        cell["badges"] = ["Absent"] if d <= yesterday else []

                tips = []
                if d in checkin_by_date:
                    tips.append(f"Check-in: {checkin_by_date[d].strftime('%H:%M')}")
                if is_holiday:
                    tips.append(f"Holiday: {holiday_map[d].name}")
                cell["tooltip"] = " • ".join(tips)

            row.append(cell)
        grid.append(row)

    context = {
        "employee_name": display_name(user),
        "days_present": present_count,
        "total_days": total_working_days,
        "year": year,
        "month": month,
        "month_name": calendar.month_name[month],
        "weeks": grid,
        "is_admin": is_admin(user),
        "is_hr": is_hr(user),
        "prev_year": prev_dt.year,
        "prev_month": prev_dt.month,
        "next_year": next_dt.year,
        "next_month": next_dt.month,
        "years": list(range(today.year - 3, today.year + 2)),
        "months": [(i, calendar.month_name[i]) for i in range(1, 13)],
        "fortune": request.session.get("fortune_msg") or random.choice(FORTUNES),
    }
    if "fortune_msg" not in request.session:
        request.session["fortune_msg"] = random.choice(FORTUNES)
    context["fortune_msg"] = request.session["fortune_msg"]

    return render(request, "dashboard.html", context)

# =========================
# Upload Attendance
# =========================
@login_required
@user_passes_test(is_admin)
def upload_attendance(request):
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f:
            messages.error(request, "Please choose a CSV or Excel file.")
            return redirect("upload_attendance")

        # --- Read CSV/Excel
        try:
            if f.name.lower().endswith(".csv"):
                df = pd.read_csv(f)
            else:
                # explicitly use openpyxl to avoid engine differences
                df = pd.read_excel(f, engine="openpyxl")
        except Exception as e:
            messages.error(request, f"Could not read file: {e}")
            return redirect("upload_attendance")

        # --- Normalize columns
        df.columns = [str(c).strip().lower() for c in df.columns]

        # --- Pick columns
        col_date = "date" if "date" in df.columns else next(
            (c for c in df.columns if "date" in c and "month" not in c and "report" not in c),
            None
        )
        in_time_candidates = ["in time", "in_time", "intime", "check in time", "check-in", "check_in"]
        col_time = next((c for c in in_time_candidates if c in df.columns), None)
        if not col_time:
            if "time" in df.columns:
                col_time = "time"
            else:
                col_time = next((c for c in df.columns if "time" in c and "out" not in c and "logout" not in c), None)
        col_email = next((c for c in df.columns if "email" in c), None)

        missing = [label for label, col in {"Date": col_date, "Time": col_time, "Email": col_email}.items() if col is None]
        if missing:
            messages.error(request, f"Missing required columns: {', '.join(missing)}")
            return redirect("upload_attendance")

        # --- Parse DATE (robust)
        try:
            df["__date"] = pd.to_datetime(df[col_date], errors="coerce", dayfirst=True).dt.date
        except Exception as e:
            messages.error(request, f"Could not parse Date column: {e}")
            return redirect("upload_attendance")

        # --- Parse EMAIL
        df["__email"] = df[col_email].astype(str).str.strip().str.lower()

        # --- Parse TIME (very robust)
        s = df[col_time]

        # 1) normalize to string where possible (strip NBSP etc.)
        s_str = s.astype(str).str.replace("\u00a0", " ", regex=False).str.strip()

        # 2) first attempt: generic parse with dayfirst/infer
        time_dt = pd.to_datetime(s_str, errors="coerce", infer_datetime_format=True, dayfirst=True)

        # 3) fallback formats commonly seen
        mask_na = time_dt.isna()
        if mask_na.any():
            # dd/mm/yyyy HH:MM:SS
            time_dt.loc[mask_na] = pd.to_datetime(s_str[mask_na], format="%d/%m/%Y %H:%M:%S", errors="coerce")
            mask_na = time_dt.isna()

        if mask_na.any():
            # mm/dd/yyyy HH:MM:SS
            time_dt.loc[mask_na] = pd.to_datetime(s_str[mask_na], format="%m/%d/%Y %H:%M:%S", errors="coerce")
            mask_na = time_dt.isna()

        if mask_na.any():
            # only time HH:MM or HH:MM:SS (no date)
            time_dt.loc[mask_na] = pd.to_datetime(s_str[mask_na], format="%H:%M:%S", errors="coerce")
            mask_na = time_dt.isna()
            time_dt.loc[mask_na] = pd.to_datetime(s_str[mask_na], format="%H:%M", errors="coerce")
            mask_na = time_dt.isna()

        if mask_na.any():
            # 4) Excel serials (numbers): interpret as days from 1899-12-30 and keep the time part
            # try converting the raw series to numeric; non-numerics become NaN
            s_num = pd.to_numeric(s_str, errors="coerce")
            serial_mask = mask_na & s_num.notna()
            if serial_mask.any():
                excel_dt = pd.to_datetime(s_num[serial_mask], unit="d", origin="1899-12-30", errors="coerce")
                # merge back
                time_dt.loc[serial_mask] = excel_dt

        # Final: we only need the time-of-day portion
        df["__time"] = time_dt.dt.time

        # --- Diagnostics BEFORE dropping null time
        total_rows = len(df)
        total_pairs_any_time = (
            df.dropna(subset=["__date", "__email"])
              .loc[:, ["__email", "__date"]]
              .drop_duplicates()
              .shape[0]
        )
        null_time_rows = df["__time"].isna().sum()

        # --- Keep rows with valid date/email/time
        df_clean = df.dropna(subset=["__date", "__time", "__email"]).copy()

        # Earliest check-in per (email, date)
        earliest = (
            df_clean.sort_values(["__email", "__date", "__time"])
                    .groupby(["__email", "__date"], as_index=False)
                    .agg({"__time": "min"})
        )

        processed_pairs = len(earliest)

        # Upsert
        created, updated = 0, 0
        for _, row in earliest.iterrows():
            eml = str(row["__email"]).lower()
            d   = row["__date"]
            t   = row["__time"]

            u = User.objects.filter(email__iexact=eml).first()
            obj, was_created = Attendance.objects.update_or_create(
                employee_email=eml,
                date=d,
                defaults={
                    "check_in_time": t,
                    "status": "P",
                    "employee": u if u else None,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        messages.success(
            request,
            (
                f"Columns used — Date: '{col_date}', Time: '{col_time}', Email: '{col_email}'. "
                f"File rows: {total_rows}. Unique (email, date) (ignoring time): {total_pairs_any_time}. "
                f"Rows with missing/unparseable time after robust parsing: {null_time_rows}. "
                f"Processed pairs: {processed_pairs}. Result: {created} created, {updated} updated."
            )
        )
        return redirect("dashboard")

    return render(request, "upload_attendance.html")


# =========================
# Defaulters
# =========================
@login_required
@user_passes_test(is_admin_or_hr)
def defaulter_list(request):
    """
    Defaulters = emails whose PRESENT days in the selected month
    are less than threshold_days. Includes people who haven't signed up yet.
    Filters:
      - year, month (defaults to current)
      - email (substring, case-insensitive)
      - threshold (optional override)
    """
    today = date.today()

    # --- Filters
    try:
        year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month

    email_q = (request.GET.get("email") or "").strip()
    threshold_override = request.GET.get("threshold")
    setting = AttendanceSetting.objects.first()
    threshold_days = setting.threshold_days if setting else 12
    if threshold_override:
        try:
            threshold_days = max(0, int(threshold_override))
        except ValueError:
            pass  # ignore bad input and keep DB value

    # --- Month bounds
    first = date(year, month, 1)
    last  = date(year, month, calendar.monthrange(year, month)[1])

    # --- Holidays & working days
    holiday_set = {h.date for h in Holiday.objects.filter(date__range=(first, last))}
    working_days = []
    cur = first
    while cur <= last:
        if cur.weekday() < 5 and cur not in holiday_set:
            working_days.append(cur)
        cur += timedelta(days=1)
    total_working_days = len(working_days) or 1

    # --- All distinct emails with any attendance rows in month (linked or unlinked)
    emails_qs = Attendance.objects.filter(date__range=(first, last))
    if email_q:
        emails_qs = emails_qs.filter(employee_email__icontains=email_q)
    emails = emails_qs.values_list("employee_email", flat=True).distinct()

    # --- Compute present-day counts on working days for each email
    defaulters = []
    for eml in emails:
        if not eml:
            continue
        present_days = (
            Attendance.objects.filter(
                employee_email__iexact=eml,
                date__range=(first, last),
                date__in=working_days,
                check_in_time__isnull=False,
            ).values("date").distinct().count()
        )
        if present_days < threshold_days:
            user = User.objects.filter(email__iexact=eml).first()
            name = (f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip()
                    if user else eml.split("@")[0])
            pct = round((present_days / total_working_days) * 100, 2)
            defaulters.append({
                "employee_name": name or eml.split("@")[0],
                "email": eml,
                "present_days": present_days,
                "total_days": total_working_days,
                "percentage": pct,
            })

    # sort by % asc, then name
    defaulters.sort(key=lambda r: (r["percentage"], r["employee_name"].lower()))

    # --- Picker helpers
    years = list(range(today.year - 3, today.year + 2))
    months = [(i, calendar.month_name[i]) for i in range(1, 13)]
    threshold_pct = round((threshold_days / total_working_days) * 100, 2)

    return render(request, "defaulters.html", {
        "defaulters": defaulters,
        "threshold": threshold_days,
        "threshold_pct": threshold_pct,
        "month": month,
        "year": year,
        "months": months,
        "years": years,
        "email_q": email_q,
    })


# =========================
# Reports
# =========================
@login_required
@user_passes_test(is_admin_or_hr)
def reports(request):
    """
    Admin/HR reports table, with Email shown for every row (linked or unlinked).
    Filters: employee (FK dropdown), start_date, end_date, and email (text).
    """
    # Employees that have at least one attendance row (for the dropdown)
    users_with_rows = User.objects.filter(
        Exists(Attendance.objects.filter(employee=OuterRef("pk")))
    ).order_by("first_name", "last_name", "username")
    employees = [{"id": u.id, "employee_name": display_name(u)} for u in users_with_rows]

    # Base query (both linked and unlinked attendance)
    qs = Attendance.objects.select_related("employee").all()

    # Filters
    emp_id    = request.GET.get("employee")
    start_str = request.GET.get("start_date")
    end_str   = request.GET.get("end_date")
    email_q   = (request.GET.get("email") or "").strip()

    if emp_id:
        qs = qs.filter(employee_id=emp_id)
    if start_str:
        qs = qs.filter(date__gte=start_str)
    if end_str:
        qs = qs.filter(date__lte=end_str)
    if email_q:
        # Match either stored canonical email or the linked user's email (case-insensitive, substring)
        qs = qs.filter(Q(employee_email__icontains=email_q) | Q(employee__email__icontains=email_q))

    # Order rows
    qs = qs.order_by("employee__first_name", "employee__last_name", "employee__username", "date")

    # Build rows for the template (include email for all)
    report_rows = []
    for r in qs:
        email_out = r.employee.email if r.employee else r.employee_email
        name_out  = display_name(r.employee) if r.employee else (email_out.split("@")[0] if email_out else "")
        report_rows.append({
            "employee_name": name_out,
            "email": email_out,
            "date": r.date,
            "status": "Present" if r.check_in_time else "Absent",
        })

    return render(request, "reports.html", {
        "employees": employees,
        "report": report_rows,
    })

# =========================
# Reports CSV Export
# =========================
@login_required
@user_passes_test(is_admin_or_hr)
def export_csv(request):
    """
    Includes both linked and unlinked rows; always outputs email.
    Optional filters: ?email=<email>&from=YYYY-MM-DD&to=YYYY-MM-DD
    """
    qs = Attendance.objects.select_related("employee").order_by("date")

    email = request.GET.get("email")
    if email:
        # --- CHANGED: filter by email field, not FK
        qs = qs.filter(employee_email__iexact=email)

    from_date = request.GET.get("from")
    to_date = request.GET.get("to")
    if from_date:
        qs = qs.filter(date__gte=from_date)
    if to_date:
        qs = qs.filter(date__lte=to_date)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="attendance_export.csv"'
    writer = csv.writer(response)
    # --- CHANGED: include Name + Email for both linked/unlinked
    writer.writerow(["Name", "Email", "Date", "Status", "Check-In"])

    for rec in qs:
        name = (
            f"{(rec.employee.first_name or '').strip()} {(rec.employee.last_name or '').strip()}".strip()
            if rec.employee else rec.employee_email.split("@")[0]
        )
        email_out = rec.employee.email if rec.employee else rec.employee_email
        status = "Present" if rec.check_in_time else "Absent"
        writer.writerow([name or email_out, email_out, rec.date.isoformat(), status, rec.check_in_time or ""])
    return response

# =========================
# Reports Export Page
# =========================
@login_required
@user_passes_test(is_admin_or_hr)
def reports_export_page(request):
    return render(request, "reports_export.html")

# =========================
# Manage Holidays
# =========================
@login_required
@user_passes_test(is_admin)
def manage_holidays(request):
    today = date.today()
    try:
        year = int(request.GET.get("year", today.year))
    except ValueError:
        year = today.year

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            h_date = parse_date(request.POST.get("date") or "")
            name = (request.POST.get("name") or "").strip()
            htype = request.POST.get("holiday_type")
            if not h_date or not name or htype not in ("PUBLIC", "RESTRICTED"):
                messages.error(request, "Please provide a valid date, name, and type.")
            else:
                try:
                    Holiday.objects.create(date=h_date, name=name, holiday_type=htype)
                    messages.success(request, f"Added: {name} ({h_date})")
                    return redirect(f"{request.path}?year={h_date.year}")
                except Exception as e:
                    messages.error(request, f"Could not add holiday: {e}")
        elif action == "delete":
            hid = request.POST.get("id")
            try:
                h = Holiday.objects.get(id=hid)
                h.delete()
                messages.success(request, "Holiday deleted.")
            except Holiday.DoesNotExist:
                messages.error(request, "Holiday not found.")
            except Exception as e:
                messages.error(request, f"Could not delete holiday: {e}")
        return redirect(f"{request.path}?year={year}")

    start_of_year = date(year, 1, 1)
    end_of_year = date(year, 12, 31)
    holidays = Holiday.objects.filter(date__range=(start_of_year, end_of_year)).order_by("date")
    return render(request, "manage_holidays.html", {"year": year, "holidays": holidays})

# =========================
# Attendance Settings
# =========================
@login_required
@user_passes_test(is_admin_or_hr)
def attendance_settings(request):
    setting, _ = AttendanceSetting.objects.get_or_create(id=1)
    if request.method == "POST":
        raw = (request.POST.get("threshold_days") or "").strip()
        try:
            setting.threshold_days = max(0, int(raw))
            setting.save()
            messages.success(request, "Threshold updated.")
        except Exception:
            messages.error(request, "Please enter a valid number.")
        return redirect("attendance_settings")
    return render(request, "attendance_settings.html", {"setting": setting})

# =========================
# Clear Attendance
# =========================
@login_required
@user_passes_test(is_admin)
@require_POST
def clear_attendance(request):
    count, _ = Attendance.objects.all().delete()
    messages.success(request, f"Cleared {count} attendance records.")
    return redirect("upload_attendance")
