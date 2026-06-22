from datetime import date, datetime, timedelta
from decimal import Decimal
import csv
import io
import json
import os
import random
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_, text
from werkzeug.security import check_password_hash, generate_password_hash


STORE_PROFILES = {
    "dadlo": {
        "software_name": "Jonam Software",
        "name": "Dadlo Fashion Retial Pvt ltd",
        "gstin": "29ABCDE1234F1Z5",
        "address": "Local Offline Store, India",
        "location": "Bangalore, Karnataka",
        "phone": "+91 98765 43210",
        "logo": "images/dadlo-fashion-transparent.png",
        "database": "retail_erp.db",
    },
    "safa": {
        "software_name": "Jonam Software",
        "name": "Safa Clothing",
        "gstin": "29DRCPB7463L1ZE",
        "address": "Ground Floor, 302/27, Chunchaghatta Main Road, Near S.M. School, Yelachenahalli, Bengaluru Urban, Karnataka - 560078",
        "location": "Bangalore, Karnataka",
        "phone": "",
        "logo": "images/safa-clothing-logo-white.png",
        "database": "safa_clothing.db",
    },
}
STORE_PROFILE = os.environ.get("STORE_PROFILE", "dadlo").lower()
STORE = STORE_PROFILES.get(STORE_PROFILE, STORE_PROFILES["dadlo"])

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "offline-retail-pos-dev-key")
database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Cloud hosts commonly provide the legacy postgres:// scheme.
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{STORE['database']}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=True)
    employee_id = db.Column(db.String(50), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    role = db.Column(db.String(20), nullable=False, default="cashier")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(140), nullable=False, index=True)
    gst_number = db.Column(db.String(30), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    state = db.Column(db.String(80), nullable=True)
    pan_number = db.Column(db.String(30), nullable=True)
    bank_details = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    products = db.relationship("Product", back_populates="vendor")


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    article_code = db.Column(db.String(80), nullable=False, index=True)
    size = db.Column(db.String(30), nullable=False)
    color = db.Column(db.String(50), nullable=False)
    cost_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    selling_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    stock_level = db.Column(db.Integer, nullable=False, default=0)
    barcode = db.Column(db.String(80), unique=True, nullable=True, index=True)
    gst_rate = db.Column(db.Numeric(5, 2), nullable=False, default=5)
    hsn_code = db.Column(db.String(30), nullable=True)
    low_stock_level = db.Column(db.Integer, nullable=False, default=5)
    image_url = db.Column(db.String(255), nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    vendor = db.relationship("Vendor", back_populates="products")
    sale_items = db.relationship("SaleItem", back_populates="product")
    movements = db.relationship("InventoryMovement", back_populates="product")

    @property
    def display_name(self):
        return f"{self.name} / {self.article_code} / {self.size} / {self.color}"

    @property
    def margin_amount(self):
        return money(self.selling_price) - money(self.cost_price)

    @property
    def margin_percent(self):
        if money(self.selling_price) == 0:
            return Decimal("0.00")
        return money(self.margin_amount * 100 / money(self.selling_price))


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    credit_balance = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    gst_number = db.Column(db.String(30), nullable=True)
    country_code = db.Column(db.String(10), nullable=True, default="+91")
    country_name = db.Column(db.String(80), nullable=True, default="India")
    religion = db.Column(db.String(80), nullable=True)
    gender = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    transactions = db.relationship("Transaction", back_populates="customer")

    @property
    def customer_code(self):
        return f"CUS-{self.phone[-6:]}"


class StoreSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    opened_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    closed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    opening_cash = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    closing_cash = db.Column(db.Numeric(10, 2), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="open")
    opened_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.String(255), nullable=True)

    opened_by = db.relationship("User", foreign_keys=[opened_by_id])
    closed_by = db.relationship("User", foreign_keys=[closed_by_id])


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bill_number = db.Column(db.String(40), unique=True, nullable=True, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)
    cashier_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    session_id = db.Column(db.Integer, db.ForeignKey("store_session.id"), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="completed")
    transaction_type = db.Column(db.String(30), nullable=False, default="sale")
    payment_method = db.Column(db.String(30), nullable=True)
    payment_tender = db.Column(db.String(60), nullable=True)
    customer_gst_number = db.Column(db.String(30), nullable=True)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    discount_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    tax_rate = db.Column(db.Numeric(5, 2), nullable=False, default=5)
    tax_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    void_reason = db.Column(db.String(255), nullable=True)
    voided_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    held_cart_json = db.Column(db.Text, nullable=True)

    customer = db.relationship("Customer", back_populates="transactions")
    cashier = db.relationship("User", foreign_keys=[cashier_id])
    voided_by = db.relationship("User", foreign_keys=[voided_by_id])
    store_session = db.relationship("StoreSession")
    items = db.relationship("SaleItem", back_populates="transaction", cascade="all, delete-orphan")


class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_cost = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    discount_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    line_total = db.Column(db.Numeric(10, 2), nullable=False, default=0)

    transaction = db.relationship("Transaction", back_populates="items")
    product = db.relationship("Product", back_populates="sale_items")


class CashTill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("store_session.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    entry_type = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    note = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    store_session = db.relationship("StoreSession")
    user = db.relationship("User")


class InventoryMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    movement_type = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    reference = db.Column(db.String(120), nullable=True)
    note = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    product = db.relationship("Product", back_populates="movements")


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def parse_money(value):
    try:
        return money(value)
    except Exception:
        return Decimal("0.00")


def demo_customer_details(index):
    first_names = ["Aarav", "Vivaan", "Aditya", "Arjun", "Kabir", "Rohan", "Ishaan", "Vihaan", "Ananya", "Aadhya", "Diya", "Ira", "Kavya", "Meera", "Nisha", "Priya"]
    last_names = ["Sharma", "Patel", "Gupta", "Reddy", "Verma", "Singh", "Nair", "Mehta", "Khan", "Das"]
    first_name = first_names[(index - 1) % len(first_names)]
    last_name = last_names[((index - 1) // len(first_names)) % len(last_names)]
    return f"{first_name} {last_name}", f"{first_name.lower()}.{last_name.lower()}{index}@example.com"


def demo_vendor_rows():
    return ["Urban Loom Traders", "North Star Apparels", "Blue Thread Fashion", "Heritage Garments", "Cotton Craft Supply", "Metro Denim House", "Luxe Lifestyle Traders", "Prime Fashion Source", "Silverline Textiles", "Velvet Wardrobe Supply", "Evergreen Apparel Co", "Noble Stitch Traders", "Classic Wear House", "Sunrise Fashion Mart", "Trendline Garments", "Royal Weave Traders", "City Style Supply", "Modern Fabric House", "Spectrum Fashion Co", "Elite Apparel Network"]


@app.template_filter("inr")
def inr(value):
    return f"Rs. {money(value):,.2f}"


def current_user():
    user_id = session.get("user_id")
    return User.query.get(user_id) if user_id else None


@app.context_processor
def inject_globals():
    return {
        "current_user": current_user(),
        "store": STORE,
        "open_session": get_open_session(),
        "login_time": session.get("login_time"),
    }


def login_required(fn):
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper


def role_required(*roles):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user or user.role not in roles:
                flash("You do not have access to this action.", "error")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)

        wrapper.__name__ = fn.__name__
        return wrapper

    return decorator


def get_open_session():
    try:
        return StoreSession.query.filter_by(status="open").order_by(StoreSession.opened_at.desc()).first()
    except Exception:
        return None


def next_bill_number(prefix="SALE"):
    return f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def next_barcode():
    last_id = (db.session.query(func.max(Product.id)).scalar() or 0) + 1
    return f"890{last_id:09d}"


def product_search(query):
    if not query:
        return []
    like_query = f"%{query.strip()}%"
    filters = [
        Product.name.ilike(like_query),
        Product.article_code.ilike(like_query),
        Product.barcode.ilike(like_query),
        Product.hsn_code.ilike(like_query),
    ]
    if query.isdigit():
        filters.append(Product.id == int(query))
    return Product.query.filter(or_(*filters)).order_by(Product.name).limit(25).all()


def customer_search(query):
    if not query:
        return Customer.query.order_by(Customer.name).limit(50).all()
    like_query = f"%{query.strip()}%"
    return Customer.query.filter(
        or_(Customer.name.ilike(like_query), Customer.phone.ilike(like_query), Customer.gst_number.ilike(like_query))
    ).order_by(Customer.name).limit(50).all()


def current_cash_balance():
    cash_in = db.session.query(func.coalesce(func.sum(CashTill.amount), 0)).filter(CashTill.entry_type == "cash_in").scalar()
    cash_out = db.session.query(func.coalesce(func.sum(CashTill.amount), 0)).filter(CashTill.entry_type == "cash_out").scalar()
    cash_sales = db.session.query(func.coalesce(func.sum(Transaction.total), 0)).filter(
        Transaction.status == "completed",
        Transaction.transaction_type == "sale",
        Transaction.payment_method == "cash_upi",
    ).scalar()
    returns = db.session.query(func.coalesce(func.sum(Transaction.total), 0)).filter(
        Transaction.status == "completed",
        Transaction.transaction_type == "return",
    ).scalar()
    return money(cash_in) + money(cash_sales) - money(cash_out) - money(returns)


def write_csv_response(filename, headers, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form["username"].strip(), is_active=True).first()
        if user and check_password_hash(user.password_hash, request.form["password"]):
            session["user_id"] = user.id
            session["login_time"] = datetime.now().strftime("%d-%m-%Y %I:%M %p")
            flash(f"Logged in as {user.username}.", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid login.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    user = current_user()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not check_password_hash(user.password_hash, current_password):
            flash("Current password is incorrect.", "error")
        elif len(new_password) < 6:
            flash("New password must contain at least 6 characters.", "error")
        elif new_password != confirm_password:
            flash("New password and confirm password do not match.", "error")
        else:
            user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash("Password changed successfully.", "success")
            return redirect(url_for("dashboard"))
    return render_template("change_password.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        user.full_name = request.form.get("full_name", "").strip()
        user.employee_id = request.form.get("employee_id", "").strip()
        user.phone = request.form.get("phone", "").strip()
        user.email = request.form.get("email", "").strip()
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


@app.route("/forgot-password")
def forgot_password():
    return render_template("forgot_password.html")


@app.route("/staff", methods=["GET", "POST"])
@login_required
@role_required("owner", "admin")
def staff_accounts():
    if request.method == "POST":
        action = request.form.get("action")
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Enter a staff username and password.", "error")
        elif len(password) < 6:
            flash("Password must contain at least 6 characters.", "error")
        elif action == "create":
            if User.query.filter_by(username=username).first():
                flash("That staff ID already exists.", "error")
            else:
                role = request.form.get("role", "cashier")
                if role not in {"admin", "cashier"}:
                    role = "cashier"
                db.session.add(User(username=username, password_hash=generate_password_hash(password), role=role))
                db.session.commit()
                flash(f"Staff ID '{username}' has been created.", "success")
        elif action == "reset":
            staff_member = User.query.filter_by(username=username, is_active=True).first()
            if not staff_member:
                flash("Active staff ID not found.", "error")
            else:
                staff_member.password_hash = generate_password_hash(password)
                db.session.commit()
                flash(f"Password reset for '{username}'.", "success")
        return redirect(url_for("staff_accounts"))

    staff = User.query.filter(User.role != "owner").order_by(User.role, User.username).all()
    return render_template("staff_accounts.html", staff=staff)


@app.route("/")
@login_required
def dashboard():
    today = date.today()
    period = request.args.get("period", "today")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    if period == "week":
        range_start = today - timedelta(days=today.weekday())
        range_end = range_start + timedelta(days=7)
        period_label = "This Week"
    elif period == "month":
        range_start = today.replace(day=1)
        range_end = (range_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        period_label = "This Month"
    elif period == "year":
        range_start = today.replace(month=1, day=1)
        range_end = today.replace(year=today.year + 1, month=1, day=1)
        period_label = "This Year"
    elif period == "last_year":
        range_start = today - timedelta(days=364)
        range_end = today + timedelta(days=1)
        period_label = "Last 12 Months"
    elif period == "range":
        try:
            range_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            range_end = datetime.strptime(end_date, "%Y-%m-%d").date() + timedelta(days=1)
            if range_end <= range_start:
                raise ValueError
            period_label = f"{range_start.strftime('%d %b %Y')} to {(range_end - timedelta(days=1)).strftime('%d %b %Y')}"
        except ValueError:
            range_start = today
            range_end = today + timedelta(days=1)
            period = "today"
            period_label = "Today"
    else:
        range_start = today
        range_end = today + timedelta(days=1)
        period = "today"
        period_label = "Today"

    start = datetime.combine(range_start, datetime.min.time())
    end = datetime.combine(range_end, datetime.min.time())
    completed = Transaction.query.filter(Transaction.status == "completed", Transaction.created_at >= start, Transaction.created_at < end)
    total_sales = completed.filter(Transaction.transaction_type == "sale").with_entities(func.coalesce(func.sum(Transaction.total), 0)).scalar()
    total_returns = completed.filter(Transaction.transaction_type == "return").with_entities(func.coalesce(func.sum(Transaction.total), 0)).scalar()
    discount_total = completed.with_entities(func.coalesce(func.sum(Transaction.discount_amount), 0)).scalar()
    tax_collected = completed.with_entities(func.coalesce(func.sum(Transaction.tax_amount), 0)).scalar()
    order_count = completed.filter(Transaction.transaction_type == "sale").count()
    profit = (
        db.session.query(func.coalesce(func.sum((SaleItem.unit_price - SaleItem.unit_cost) * SaleItem.quantity - SaleItem.discount_amount), 0))
        .join(Transaction)
        .filter(Transaction.status == "completed", Transaction.transaction_type == "sale", Transaction.created_at >= start, Transaction.created_at <= end)
        .scalar()
    )
    margin = money(profit * 100 / total_sales) if money(total_sales) else Decimal("0.00")
    top_items = (
        db.session.query(Product.name, Product.article_code, func.sum(SaleItem.quantity).label("qty"), func.sum(SaleItem.line_total).label("revenue"))
        .join(SaleItem)
        .join(Transaction)
        .filter(Transaction.status == "completed", Transaction.transaction_type == "sale", Transaction.created_at >= start, Transaction.created_at <= end)
        .group_by(Product.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(10)
        .all()
    )
    low_stock = Product.query.filter(Product.stock_level <= Product.low_stock_level).order_by(Product.stock_level.asc()).limit(10).all()
    chart_sales = money(total_sales)
    chart_returns = money(total_returns)
    chart_discount = money(discount_total)
    chart_total = chart_sales + chart_returns + chart_discount
    if chart_total:
        chart_sales_percent = int((chart_sales * 100 / chart_total).quantize(Decimal("1")))
        chart_returns_percent = int((chart_returns * 100 / chart_total).quantize(Decimal("1")))
    else:
        chart_sales_percent = 100
        chart_returns_percent = 0
    chart_discount_percent = max(0, 100 - chart_sales_percent - chart_returns_percent)
    return render_template(
        "dashboard.html",
        total_sales=money(total_sales),
        total_returns=money(total_returns),
        total_profit=money(profit),
        margin=margin,
        discount_total=money(discount_total),
        tax_collected=money(tax_collected),
        order_count=order_count,
        cash_balance=current_cash_balance(),
        top_items=top_items,
        low_stock=low_stock,
        chart_sales_percent=chart_sales_percent,
        chart_returns_percent=chart_returns_percent,
        chart_discount_percent=chart_discount_percent,
        period=period,
        period_label=period_label,
        start_date=start_date or range_start.isoformat(),
        end_date=end_date or (range_end - timedelta(days=1)).isoformat(),
    )


@app.route("/store-music")
@login_required
def store_music():
    return render_template("store_music.html")


@app.route("/gst-returns")
@login_required
@role_required("owner", "admin")
def gst_returns():
    selected_month = request.args.get("month") or date.today().strftime("%Y-%m")
    try:
        year, month = [int(value) for value in selected_month.split("-", 1)]
        period_start = datetime(year, month, 1)
    except (ValueError, TypeError):
        selected_month = date.today().strftime("%Y-%m")
        period_start = datetime(date.today().year, date.today().month, 1)

    if period_start.month == 12:
        period_end = datetime(period_start.year + 1, 1, 1)
    else:
        period_end = datetime(period_start.year, period_start.month + 1, 1)

    transactions = Transaction.query.filter(
        Transaction.status == "completed",
        Transaction.created_at >= period_start,
        Transaction.created_at < period_end,
    ).order_by(Transaction.created_at.desc()).all()
    sales = [tx for tx in transactions if tx.transaction_type == "sale"]
    adjustments = [tx for tx in transactions if tx.transaction_type in {"return", "credit_note"}]
    b2b_sales = [tx for tx in sales if tx.customer_gst_number]
    b2c_sales = [tx for tx in sales if not tx.customer_gst_number]

    taxable_sales = sum((money(tx.subtotal) - money(tx.discount_amount) for tx in sales), Decimal("0.00"))
    output_tax = sum((money(tx.tax_amount) for tx in sales), Decimal("0.00"))
    adjustment_tax = sum((money(tx.tax_amount) for tx in adjustments), Decimal("0.00"))
    adjustment_total = sum((money(tx.total) for tx in adjustments), Decimal("0.00"))

    return render_template(
        "gst_returns.html",
        selected_month=selected_month,
        period_start=period_start,
        sales=sales,
        b2b_sales=b2b_sales,
        b2c_sales=b2c_sales,
        adjustments=adjustments,
        taxable_sales=money(taxable_sales),
        output_tax=money(output_tax),
        adjustment_tax=money(adjustment_tax),
        adjustment_total=money(adjustment_total),
        net_tax=money(output_tax - adjustment_tax),
    )


@app.route("/gst-returns/export")
@login_required
@role_required("owner", "admin")
def export_gst_return():
    selected_month = request.args.get("month") or date.today().strftime("%Y-%m")
    try:
        year, month = [int(value) for value in selected_month.split("-", 1)]
        period_start = datetime(year, month, 1)
    except (ValueError, TypeError):
        selected_month = date.today().strftime("%Y-%m")
        period_start = datetime(date.today().year, date.today().month, 1)

    period_end = datetime(period_start.year + 1, 1, 1) if period_start.month == 12 else datetime(period_start.year, period_start.month + 1, 1)
    documents = Transaction.query.filter(
        Transaction.status == "completed",
        Transaction.transaction_type.in_(["sale", "return", "credit_note"]),
        Transaction.created_at >= period_start,
        Transaction.created_at < period_end,
    ).order_by(Transaction.created_at).all()
    headers = ["invoice_date", "invoice_number", "document_type", "customer_name", "customer_gstin", "taxable_value", "gst_rate", "gst_amount", "invoice_total", "payment_method", "payment_tender"]
    rows = [
        [
            tx.created_at.strftime("%Y-%m-%d"),
            tx.bill_number,
            tx.transaction_type.replace("_", " ").title(),
            tx.customer.name if tx.customer else "Walk-in Customer",
            tx.customer_gst_number or "",
            money(tx.subtotal) - money(tx.discount_amount),
            tx.tax_rate,
            tx.tax_amount,
            tx.total,
            tx.payment_method or "",
            tx.payment_tender or "",
        ]
        for tx in documents
    ]
    return write_csv_response(f"gst_return_{selected_month}.csv", headers, rows)


@app.route("/music/stations")
@login_required
def music_stations():
    """Return free public radio stations for the in-app music player."""
    role = current_user().role
    mood = request.args.get("mood", "retail")
    query = request.args.get("q", "").strip()
    approved_tags = {
        "retail": "chillout",
        "calm": "ambient",
        "jazz": "jazz",
    }

    if role not in {"owner", "admin"} or not query:
        tag = approved_tags.get(mood, "chillout")
        endpoint = f"https://de1.api.radio-browser.info/json/stations/bytag/{quote(tag)}?hidebroken=true&order=votes&reverse=true&limit=12"
    else:
        endpoint = f"https://de1.api.radio-browser.info/json/stations/search?name={quote(query)}&hidebroken=true&order=votes&reverse=true&limit=20"

    try:
        radio_request = Request(endpoint, headers={"User-Agent": "JonamSoftware/1.0"})
        with urlopen(radio_request, timeout=8) as response:
            stations = json.loads(response.read().decode("utf-8"))
        cleaned = [
            {
                "name": station.get("name") or "Unnamed station",
                "country": station.get("country") or "",
                "tags": station.get("tags") or "",
                "url": station.get("url_resolved") or station.get("url") or "",
            }
            for station in stations
            if station.get("url_resolved") or station.get("url")
        ]
        return jsonify({"stations": cleaned})
    except Exception:
        return jsonify({"stations": [], "error": "Unable to load radio stations. Check your internet connection."}), 503


@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    if request.method == "POST":
        barcode = request.form.get("barcode", "").strip() or next_barcode()
        product = Product(
            name=request.form["name"].strip(),
            article_code=request.form["article_code"].strip(),
            size=request.form["size"].strip(),
            color=request.form["color"].strip(),
            cost_price=parse_money(request.form["cost_price"]),
            selling_price=parse_money(request.form["selling_price"]),
            stock_level=int(request.form["stock_level"] or 0),
            barcode=barcode,
            gst_rate=parse_money(request.form.get("gst_rate", 5)),
            hsn_code=request.form.get("hsn_code", "").strip(),
            low_stock_level=int(request.form.get("low_stock_level", 5) or 5),
            image_url=request.form.get("image_url", "").strip(),
            vendor_id=request.form.get("vendor_id") or None,
        )
        db.session.add(product)
        db.session.flush()
        db.session.add(InventoryMovement(product=product, movement_type="opening", quantity=product.stock_level, reference="Product create"))
        db.session.commit()
        flash(f"Product added. Barcode: {product.barcode}", "success")
        return redirect(url_for("products"))

    query = request.args.get("q", "").strip()
    items = product_search(query) if query else Product.query.order_by(Product.name).limit(100).all()
    return render_template("products.html", products=items, vendors=Vendor.query.order_by(Vendor.name).all(), query=query)


@app.route("/barcode-labels")
@login_required
def barcode_labels():
    product_id = request.args.get("product_id", type=int)
    quantity = max(1, min(request.args.get("quantity", 1, type=int), 100))
    product = Product.query.get_or_404(product_id) if product_id else None
    return render_template("barcode_labels.html", product=product, quantity=quantity)


@app.route("/vendors", methods=["GET", "POST"])
@login_required
def vendors():
    if request.method == "POST":
        vendor = Vendor(
            name=request.form["name"].strip(),
            gst_number=request.form.get("gst_number", "").strip(),
            phone=request.form.get("phone", "").strip(),
            email=request.form.get("email", "").strip(),
            address=request.form.get("address", "").strip(),
            state=request.form.get("state", "").strip(),
            pan_number=request.form.get("pan_number", "").strip(),
            bank_details=request.form.get("bank_details", "").strip(),
        )
        db.session.add(vendor)
        db.session.commit()
        flash("Vendor saved.", "success")
        return redirect(url_for("vendors"))
    return render_template("vendors.html", vendors=Vendor.query.order_by(Vendor.name).all())


@app.route("/seed-demo-vendors", methods=["POST"])
@login_required
@role_required("owner", "admin")
def seed_demo_vendors():
    created = 0
    for index, name in enumerate(demo_vendor_rows(), start=1):
        if Vendor.query.filter_by(name=name).first():
            continue
        db.session.add(Vendor(name=name, gst_number=f"29DFV{index:07d}F1Z{index % 10}", phone=f"08040{index:05d}", email=f"sales{index}@vendor.example.com", state="Karnataka", pan_number=f"DFV{index:07d}K"))
        created += 1
    db.session.commit()
    flash(f"Added {created} fashion vendors.", "success")
    return redirect(url_for("vendors"))


@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    if request.method == "POST":
        customer = Customer(
            name=request.form["name"].strip(),
            phone=request.form["phone"].strip(),
            credit_balance=parse_money(request.form.get("credit_balance", 0)),
            gst_number=request.form.get("gst_number", "").strip(),
            country_code=request.form.get("country_code", "").strip(),
            country_name=request.form.get("country_name", "").strip(),
            religion=request.form.get("religion", "").strip(),
            gender=request.form.get("gender", "").strip(),
            email=request.form.get("email", "").strip(),
            address=request.form.get("address", "").strip(),
        )
        db.session.add(customer)
        db.session.commit()
        flash("Customer registered.", "success")
        return redirect(url_for("customers", q=customer.phone))
    query = request.args.get("q", "").strip()
    selected = Customer.query.get(request.args.get("id")) if request.args.get("id") else None
    return render_template("customers.html", customers=customer_search(query), selected=selected, query=query)


@app.route("/customer/<int:customer_id>/history")
@login_required
def customer_history(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    transactions = Transaction.query.filter_by(customer_id=customer.id).order_by(Transaction.created_at.desc()).all()
    return render_template("customer_history.html", customer=customer, transactions=transactions)


@app.route("/cash-till", methods=["GET", "POST"])
@login_required
def cash_till():
    if request.method == "POST":
        entry = CashTill(
            session_id=get_open_session().id if get_open_session() else None,
            user_id=session.get("user_id"),
            entry_type=request.form["entry_type"],
            amount=parse_money(request.form["amount"]),
            note=request.form["note"].strip(),
        )
        db.session.add(entry)
        db.session.commit()
        flash("Cash till entry saved.", "success")
        return redirect(url_for("cash_till"))
    entries = CashTill.query.order_by(CashTill.created_at.desc()).limit(100).all()
    return render_template("cash_till.html", entries=entries, balance=current_cash_balance(), sessions=StoreSession.query.order_by(StoreSession.opened_at.desc()).limit(20).all())


@app.route("/sessions/open", methods=["POST"])
@login_required
def open_store_session():
    if get_open_session():
        flash("A store session is already open.", "error")
        return redirect(url_for("cash_till"))
    store_session = StoreSession(opened_by_id=session.get("user_id"), opening_cash=parse_money(request.form["opening_cash"]), note=request.form.get("note", ""))
    db.session.add(store_session)
    db.session.flush()
    db.session.add(CashTill(session_id=store_session.id, user_id=session.get("user_id"), entry_type="cash_in", amount=store_session.opening_cash, note="Opening cash"))
    db.session.commit()
    flash("Store session opened.", "success")
    return redirect(url_for("cash_till"))


@app.route("/sessions/close", methods=["POST"])
@login_required
def close_store_session():
    store_session = get_open_session()
    if not store_session:
        flash("No open session found.", "error")
        return redirect(url_for("cash_till"))
    store_session.closing_cash = parse_money(request.form["closing_cash"])
    store_session.closed_by_id = session.get("user_id")
    store_session.closed_at = datetime.utcnow()
    store_session.status = "closed"
    db.session.commit()
    flash("Store session closed.", "success")
    return redirect(url_for("cash_till"))


@app.route("/pos")
@login_required
def pos():
    query = request.args.get("q", "").strip()
    phone = request.args.get("phone", "").strip()
    products_found = product_search(query) if query else Product.query.order_by(Product.name).limit(80).all()
    customers_found = customer_search(phone) if phone else Customer.query.order_by(Customer.name).limit(50).all()
    held_bills = Transaction.query.filter_by(status="held").order_by(Transaction.created_at.desc()).all()
    return render_template("pos.html", products_found=products_found, query=query, phone=phone, customers_found=customers_found, held_bills=held_bills, tax_rate="5")


@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    cart = json.loads(request.form.get("cart_json") or "[]")
    action = request.form.get("action")
    tx_type = request.form.get("transaction_type", "sale")
    payment_method = request.form.get("payment_method")
    payment_tender = request.form.get("payment_tender", "").strip()
    discount_amount = parse_money(request.form.get("discount_amount", 0))
    tax_rate = parse_money(request.form.get("tax_rate", 5))
    if not cart:
        flash("Cart is empty.", "error")
        return redirect(url_for("pos"))
    if action != "hold" and not payment_tender:
        flash("Payment tender is mandatory.", "error")
        return redirect(url_for("pos"))

    customer = None
    customer_id = request.form.get("customer_id") or None
    if customer_id:
        customer = Customer.query.get(int(customer_id))
    elif request.form.get("new_customer_name") and request.form.get("new_customer_phone"):
        customer = Customer.query.filter_by(phone=request.form["new_customer_phone"].strip()).first()
        if not customer:
            customer = Customer(
                name=request.form["new_customer_name"].strip(),
                phone=request.form["new_customer_phone"].strip(),
                gst_number=request.form.get("customer_gst_number", "").strip(),
            )
            db.session.add(customer)
            db.session.flush()

    if action == "hold":
        held = Transaction(customer=customer, status="held", subtotal=0, tax_rate=tax_rate, held_cart_json=json.dumps(cart), cashier_id=session.get("user_id"))
        db.session.add(held)
        db.session.commit()
        flash("Bill held.", "success")
        return redirect(url_for("pos"))
    if payment_method == "credit" and not customer:
        flash("Customer is required for Khata credit.", "error")
        return redirect(url_for("pos"))

    prefix = {"sale": "SALE", "return": "RET", "quotation": "QUO", "credit_note": "CN", "debit_note": "DN"}.get(tx_type, "SALE")
    subtotal = Decimal("0.00")
    tx = Transaction(
        bill_number=next_bill_number(prefix),
        customer=customer,
        cashier_id=session.get("user_id"),
        session_id=get_open_session().id if get_open_session() else None,
        status="completed",
        transaction_type=tx_type,
        payment_method=payment_method,
        payment_tender=payment_tender,
        discount_amount=discount_amount,
        tax_rate=tax_rate,
        customer_gst_number=request.form.get("customer_gst_number", "").strip() or (customer.gst_number if customer else ""),
    )
    db.session.add(tx)
    db.session.flush()

    for row in cart:
        product = Product.query.get(int(row["product_id"]))
        qty = int(row["quantity"])
        if not product or qty <= 0:
            continue
        if tx_type in ["sale", "quotation", "debit_note"] and product.stock_level < qty and tx_type != "quotation":
            db.session.rollback()
            flash(f"Not enough stock for {product.display_name}.", "error")
            return redirect(url_for("pos"))
        line_total = money(product.selling_price) * qty
        subtotal += line_total
        if tx_type in ["sale", "debit_note"]:
            product.stock_level -= qty
            db.session.add(InventoryMovement(product=product, movement_type="sale", quantity=-qty, reference=tx.bill_number))
        elif tx_type in ["return", "credit_note"]:
            product.stock_level += qty
            db.session.add(InventoryMovement(product=product, movement_type="return", quantity=qty, reference=tx.bill_number))
        db.session.add(SaleItem(transaction=tx, product=product, quantity=qty, unit_cost=product.cost_price, unit_price=product.selling_price, line_total=line_total))

    taxable = max(Decimal("0.00"), subtotal - discount_amount)
    tx.subtotal = money(subtotal)
    tx.tax_amount = money(taxable * tax_rate / Decimal("100"))
    tx.total = money(taxable + tx.tax_amount)
    if tx_type == "return":
        tx.total = money(tx.total)
    if payment_method == "credit" and customer and tx_type == "sale":
        customer.credit_balance = money(customer.credit_balance) + tx.total
    if tx_type in ["return", "credit_note"] and customer:
        customer.credit_balance = max(Decimal("0.00"), money(customer.credit_balance) - tx.total)
    db.session.commit()
    flash(f"{tx.transaction_type.replace('_', ' ').title()} saved. Bill {tx.bill_number}.", "success")
    return redirect(url_for("print_bill", transaction_id=tx.id))


@app.route("/recall/<int:transaction_id>")
@login_required
def recall(transaction_id):
    held = Transaction.query.get_or_404(transaction_id)
    raw_cart = json.loads(held.held_cart_json or "[]")
    recalled_cart = []
    for row in raw_cart:
        product = Product.query.get(int(row["product_id"]))
        if product:
            recalled_cart.append({"product_id": product.id, "label": product.display_name, "quantity": int(row["quantity"]), "price": float(product.selling_price), "stock": product.stock_level})
    customer_id = held.customer_id or ""
    tax_rate = str(held.tax_rate)
    db.session.delete(held)
    db.session.commit()
    return render_template("pos.html", products_found=[], query="", phone="", customers_found=[], held_bills=Transaction.query.filter_by(status="held").all(), tax_rate=tax_rate, recalled_cart=recalled_cart, recalled_customer_id=customer_id)


@app.route("/bill/<int:transaction_id>")
@login_required
def print_bill(transaction_id):
    tx = Transaction.query.get_or_404(transaction_id)
    return render_template("bill_print.html", tx=tx)


@app.route("/void/<int:transaction_id>", methods=["POST"])
@login_required
@role_required("owner")
def void_bill(transaction_id):
    tx = Transaction.query.get_or_404(transaction_id)
    if tx.status == "void":
        flash("Bill already voided.", "error")
        return redirect(url_for("print_bill", transaction_id=tx.id))
    tx.status = "void"
    tx.void_reason = request.form.get("void_reason", "").strip()
    tx.voided_by_id = session.get("user_id")
    if tx.transaction_type == "sale":
        for item in tx.items:
            item.product.stock_level += item.quantity
            db.session.add(InventoryMovement(product=item.product, movement_type="void", quantity=item.quantity, reference=tx.bill_number))
    db.session.commit()
    flash("Bill voided by owner.", "success")
    return redirect(url_for("print_bill", transaction_id=tx.id))


@app.route("/inventory-history")
@login_required
def inventory_history():
    product_id = request.args.get("product_id")
    query = InventoryMovement.query.order_by(InventoryMovement.created_at.desc())
    if product_id:
        query = query.filter_by(product_id=product_id)
    return render_template("inventory_history.html", movements=query.limit(300).all(), products=Product.query.order_by(Product.name).all())


@app.route("/reports/closing")
@login_required
def closing_report():
    transactions = Transaction.query.filter(Transaction.status == "completed").order_by(Transaction.created_at.desc()).limit(200).all()
    return render_template("closing_report.html", transactions=transactions, cash_balance=current_cash_balance())


@app.route("/data")
@login_required
def data_tools():
    return render_template("data_tools.html")


@app.route("/export/<kind>")
@login_required
def export_data(kind):
    if kind == "customers":
        headers = ["name", "phone", "gst_number", "country_code", "country_name", "religion", "gender", "email", "address", "credit_balance"]
        rows = [[c.name, c.phone, c.gst_number, c.country_code, c.country_name, c.religion, c.gender, c.email, c.address, c.credit_balance] for c in Customer.query.all()]
    elif kind == "low_stock":
        headers = ["barcode", "name", "article_code", "size", "color", "stock_level", "low_stock_level", "vendor"]
        rows = [[p.barcode, p.name, p.article_code, p.size, p.color, p.stock_level, p.low_stock_level, p.vendor.name if p.vendor else ""] for p in Product.query.filter(Product.stock_level <= Product.low_stock_level).all()]
    elif kind == "vendors":
        headers = ["name", "gst_number", "phone", "email", "address", "state", "pan_number", "bank_details"]
        rows = [[v.name, v.gst_number, v.phone, v.email, v.address, v.state, v.pan_number, v.bank_details] for v in Vendor.query.order_by(Vendor.name).all()]
    else:
        headers = ["barcode", "name", "article_code", "size", "color", "cost_price", "selling_price", "mrp", "stock_level", "gst_rate", "vendor", "hsn_code"]
        rows = [[p.barcode, p.name, p.article_code, p.size, p.color, p.cost_price, p.selling_price, p.selling_price, p.stock_level, p.gst_rate, p.vendor.name if p.vendor else "", p.hsn_code] for p in Product.query.all()]
    return write_csv_response(f"{kind}.csv", headers, rows)


@app.route("/import/products", methods=["POST"])
@login_required
def import_products():
    file = request.files.get("file")
    if not file:
        flash("Upload a CSV file.", "error")
        return redirect(url_for("products"))
    reader = csv.DictReader(io.StringIO(file.read().decode("utf-8-sig")))
    count = 0
    for row in reader:
        vendor = None
        if row.get("vendor"):
            vendor = Vendor.query.filter_by(name=row["vendor"].strip()).first() or Vendor(name=row["vendor"].strip())
            db.session.add(vendor)
            db.session.flush()
        product = Product(
            barcode=row.get("barcode") or next_barcode(),
            name=row.get("name", "").strip(),
            article_code=row.get("article_code", "").strip(),
            size=row.get("size", "").strip(),
            color=row.get("color", "").strip(),
            cost_price=parse_money(row.get("cost_price", 0)),
            selling_price=parse_money(row.get("selling_price") or row.get("mrp") or 0),
            stock_level=int(row.get("stock_level") or 0),
            gst_rate=parse_money(row.get("gst_rate", 5)),
            vendor=vendor,
            hsn_code=row.get("hsn_code", ""),
        )
        db.session.add(product)
        count += 1
    db.session.commit()
    flash(f"Imported {count} products.", "success")
    return redirect(url_for("products"))


@app.route("/import/customers", methods=["POST"])
@login_required
def import_customers():
    file = request.files.get("file")
    if not file:
        flash("Upload a CSV file.", "error")
        return redirect(url_for("customers"))
    reader = csv.DictReader(io.StringIO(file.read().decode("utf-8-sig")))
    count = 0
    for row in reader:
        if Customer.query.filter_by(phone=row.get("phone", "").strip()).first():
            continue
        db.session.add(Customer(name=row.get("name", "").strip(), phone=row.get("phone", "").strip(), gst_number=row.get("gst_number", ""), country_code=row.get("country_code", "+91"), country_name=row.get("country_name", "India"), religion=row.get("religion", ""), gender=row.get("gender", ""), email=row.get("email", ""), address=row.get("address", ""), credit_balance=parse_money(row.get("credit_balance", 0))))
        count += 1
    db.session.commit()
    flash(f"Imported {count} customers.", "success")
    return redirect(url_for("customers"))


@app.route("/import/vendors", methods=["POST"])
@login_required
def import_vendors():
    file = request.files.get("file")
    if not file:
        flash("Upload a CSV file.", "error")
        return redirect(url_for("vendors"))
    reader = csv.DictReader(io.StringIO(file.read().decode("utf-8-sig")))
    count = 0
    for row in reader:
        name = row.get("name", "").strip()
        if not name or Vendor.query.filter_by(name=name).first():
            continue
        db.session.add(
            Vendor(
                name=name,
                gst_number=row.get("gst_number", "").strip(),
                phone=row.get("phone", "").strip(),
                email=row.get("email", "").strip(),
                address=row.get("address", "").strip(),
                state=row.get("state", "").strip(),
                pan_number=row.get("pan_number", "").strip(),
                bank_details=row.get("bank_details", "").strip(),
            )
        )
        count += 1
    db.session.commit()
    flash(f"Imported {count} vendors.", "success")
    return redirect(url_for("vendors"))


@app.route("/seed-test-data")
@login_required
@role_required("owner", "admin")
def seed_test_data():
    for i in range(1, 101):
        phone = f"90000{i:05d}"
        if not Customer.query.filter_by(phone=phone).first():
            customer_name, customer_email = demo_customer_details(i)
            db.session.add(Customer(name=customer_name, phone=phone, country_code="+91", country_name="India", gender=random.choice(["Male", "Female", "Other"]), religion=random.choice(["Hindu", "Muslim", "Christian", "Sikh", "Other"]), email=customer_email))
    vendor = Vendor.query.filter_by(name="Dadlo Fashion Supply House").first() or Vendor(name="Dadlo Fashion Supply House", gst_number="29SAMPLE1234F1Z1", state="Karnataka")
    db.session.add(vendor)
    db.session.flush()
    names = ["Graphic Tee", "Oversized Hoodie", "Cotton Jogger", "Slim Denim", "Casual Shirt", "Polo T-Shirt", "Cargo Pant", "Denim Jacket"]
    colors = ["Black", "White", "Navy", "Olive", "Maroon", "Blue"]
    sizes = ["S", "M", "L", "XL", "32", "34"]
    for i in range(1, 81):
        product = Product(name=random.choice(names), article_code=f"DF-{1000+i}", size=random.choice(sizes), color=random.choice(colors), cost_price=random.randint(250, 1200), selling_price=random.randint(699, 2999), stock_level=random.randint(3, 40), barcode=next_barcode(), gst_rate=5, vendor=vendor, low_stock_level=5)
        db.session.add(product)
    db.session.commit()
    flash("Dummy 100 customers and sample fashion inventory created.", "success")
    return redirect(url_for("data_tools"))


@app.route("/seed-test-bills", methods=["POST"])
@login_required
@role_required("owner", "admin")
def seed_test_bills():
    if Transaction.query.filter(Transaction.bill_number.like("DF-%")).first():
        flash("Two-month test bills already exist. Delete the test database before creating them again.", "error")
        return redirect(url_for("data_tools"))

    products = Product.query.filter(Product.stock_level > 0).all()
    customers = Customer.query.all()
    inventory_created = False
    if not customers:
        for i in range(1, 101):
            customer_name, customer_email = demo_customer_details(i)
            db.session.add(
                Customer(
                    name=customer_name,
                    phone=f"90000{i:05d}",
                    country_code="+91",
                    country_name="India",
                    gender=random.choice(["Male", "Female", "Other"]),
                    religion=random.choice(["Hindu", "Muslim", "Christian", "Sikh", "Other"]),
                    email=customer_email,
                )
            )
        db.session.flush()
        customers = Customer.query.all()

    if not products:
        vendor = Vendor.query.filter_by(name="Dadlo Fashion Supply House").first() or Vendor(
            name="Dadlo Fashion Supply House", gst_number="29SAMPLE1234F1Z1", state="Karnataka"
        )
        db.session.add(vendor)
        db.session.flush()
        names = ["Graphic Tee", "Oversized Hoodie", "Cotton Jogger", "Slim Denim", "Casual Shirt", "Polo T-Shirt", "Cargo Pant", "Denim Jacket"]
        colors = ["Black", "White", "Navy", "Olive", "Maroon", "Blue"]
        sizes = ["S", "M", "L", "XL", "32", "34"]
        for i in range(1, 81):
            db.session.add(
                Product(
                    name=random.choice(names),
                    article_code=f"DF-{1000+i}",
                    size=random.choice(sizes),
                    color=random.choice(colors),
                    cost_price=random.randint(250, 1200),
                    selling_price=random.randint(699, 2999),
                    stock_level=random.randint(12, 40),
                    barcode=next_barcode(),
                    gst_rate=5,
                    vendor=vendor,
                    low_stock_level=5,
                )
            )
        db.session.flush()
        products = Product.query.filter(Product.stock_level > 0).all()
        inventory_created = True

    bill_count = 0
    now = datetime.now()
    for days_ago in range(60):
        sale_date = now - timedelta(days=days_ago)
        for bill_index in range(random.randint(1, 3)):
            available_products = [product for product in products if product.stock_level > 0]
            if not available_products:
                break

            sale_time = sale_date.replace(
                hour=random.randint(10, 20), minute=random.randint(0, 59), second=random.randint(0, 59), microsecond=0
            )
            selected_products = random.sample(available_products, min(len(available_products), random.randint(1, 4)))
            transaction = Transaction(
                bill_number=f"DF-{sale_time.strftime('%Y%m%d')}-{bill_index + 1:02d}",
                customer=random.choice(customers),
                cashier_id=current_user().id,
                status="completed",
                transaction_type="sale",
                payment_method="upi",
                payment_tender="Test UPI",
                tax_rate=Decimal("5.00"),
                created_at=sale_time,
            )
            db.session.add(transaction)
            subtotal = Decimal("0.00")

            for product in selected_products:
                quantity = random.randint(1, min(2, product.stock_level))
                unit_price = money(product.selling_price)
                line_total = money(unit_price * quantity)
                subtotal += line_total
                product.stock_level -= quantity
                transaction.items.append(
                    SaleItem(
                        product=product,
                        quantity=quantity,
                        unit_cost=money(product.cost_price),
                        unit_price=unit_price,
                        line_total=line_total,
                    )
                )
                db.session.add(
                    InventoryMovement(
                        product=product,
                        movement_type="test_sale",
                        quantity=-quantity,
                        reference=transaction.bill_number,
                        note="Generated 60-day sales data",
                        created_at=sale_time,
                    )
                )

            discount_amount = money(subtotal * Decimal(random.choice(["0", "0", "0.03", "0.05"])))
            taxable_amount = subtotal - discount_amount
            tax_amount = money(taxable_amount * Decimal("0.05"))
            transaction.subtotal = money(subtotal)
            transaction.discount_amount = discount_amount
            transaction.tax_amount = tax_amount
            transaction.total = money(taxable_amount + tax_amount)
            bill_count += 1

    db.session.commit()
    message = f"Created {bill_count} completed test bills across the last 60 days."
    if inventory_created:
        message += " Dummy fashion inventory and customers were also created."
    flash(message, "success")
    return redirect(url_for("data_tools"))


@app.route("/reset-demo-data", methods=["POST"])
@login_required
@role_required("owner", "admin")
def reset_demo_data():
    # Clear business records only. Staff login accounts are intentionally retained.
    db.session.query(SaleItem).delete()
    db.session.query(InventoryMovement).delete()
    db.session.query(Transaction).delete()
    db.session.query(CashTill).delete()
    db.session.query(StoreSession).delete()
    db.session.query(Product).delete()
    db.session.query(Customer).delete()
    db.session.query(Vendor).delete()
    db.session.flush()

    vendors = []
    for index, name in enumerate(demo_vendor_rows(), start=1):
        vendor = Vendor(name=name, gst_number=f"29DFV{index:07d}F1Z{index % 10}", state="Karnataka", phone=f"08040{index:05d}", email=f"sales{index}@vendor.example.com", pan_number=f"DFV{index:07d}K")
        vendors.append(vendor)
        db.session.add(vendor)
    customer_rows = [
        ("Aarav Sharma", "9876543210"), ("Ananya Patel", "9865321470"), ("Meera Reddy", "9845012345"), ("Kabir Singh", "9811122233"), ("Priya Nair", "9898987654"),
        ("Rohan Mehta", "9822012345"), ("Diya Verma", "9900012345"), ("Ishaan Gupta", "9888801234"), ("Kavya Das", "9833301234"), ("Vivaan Khan", "9855501234"),
    ]
    customers = []
    for index, (name, phone) in enumerate(customer_rows, start=1):
        customer = Customer(name=name, phone=phone, country_code="+91", country_name="India", gender="Female" if index % 2 == 0 else "Male", email=f"{name.lower().replace(' ', '.')}@example.com")
        customers.append(customer)
        db.session.add(customer)

    product_names = ["Cotton Crew T-Shirt", "Slim Fit Formal Shirt", "Linen Casual Shirt", "Polo Collar T-Shirt", "Oversized Graphic Tee", "Denim Jacket", "Bomber Jacket", "Cotton Hoodie", "Zip-Up Sweatshirt", "Chino Trouser", "Slim Fit Denim", "Cargo Pant", "Cotton Jogger", "Formal Trouser", "Track Pant", "Printed Kurti", "Rayon Kurti", "Cotton Saree Blouse", "Casual Top", "Longline Shrug", "Pleated Skirt", "Denim Skirt", "Cotton Palazzo", "Leggings", "Summer Dress", "Floral Dress", "Casual Blazer", "Waistcoat", "V-Neck Sweater", "Cardigan", "Checked Shirt", "Striped Shirt", "Oxford Shirt", "Cargo Shorts", "Denim Shorts", "Puffer Jacket", "Windcheater", "Henley T-Shirt", "Round Neck T-Shirt", "Mandarin Collar Shirt", "Linen Trouser", "Wide Leg Pant", "Ankle Length Jeans", "Tapered Jeans", "Cropped Hoodie", "Basic Tank Top", "Printed Shirt", "Casual Co-ord Set", "Office Wear Top", "Weekend Jacket"]
    colors = ["Black", "White", "Navy", "Olive", "Maroon", "Blue", "Beige", "Grey"]
    sizes = ["S", "M", "L", "XL", "32", "34", "36"]
    products = []
    for index, name in enumerate(product_names, start=1):
        cost = Decimal(random.randint(350, 1500))
        product = Product(name=name, article_code=f"DF-{1000 + index}", size=random.choice(sizes), color=random.choice(colors), cost_price=cost, selling_price=cost + Decimal(random.choice([400, 500, 600, 700, 900])), stock_level=650, barcode=f"890{700000000 + index}", gst_rate=Decimal("5.00"), vendor=random.choice(vendors), low_stock_level=25)
        products.append(product)
        db.session.add(product)
    db.session.flush()
    for product in products:
        db.session.add(InventoryMovement(product=product, movement_type="opening", quantity=650, reference="Demo opening stock", note="One-year demo data"))

    bill_count = 0
    now = datetime.now()
    for days_ago in range(365):
        sale_date = now - timedelta(days=days_ago)
        for bill_index in range(random.randint(6, 10)):
            available_products = [product for product in products if product.stock_level > 2]
            if not available_products:
                break
            sale_time = sale_date.replace(hour=random.randint(10, 20), minute=random.randint(0, 59), second=random.randint(0, 59), microsecond=0)
            selected_products = random.sample(available_products, min(len(available_products), random.randint(1, 3)))
            transaction = Transaction(bill_number=f"DF-{sale_time.strftime('%Y%m%d')}-{bill_index + 1:02d}", customer=random.choice(customers), cashier_id=current_user().id, status="completed", transaction_type="sale", payment_method="upi", payment_tender="UPI", tax_rate=Decimal("5.00"), created_at=sale_time)
            db.session.add(transaction)
            subtotal = Decimal("0.00")
            for product in selected_products:
                quantity = random.randint(1, 2)
                unit_price = money(product.selling_price)
                line_total = money(unit_price * quantity)
                subtotal += line_total
                product.stock_level -= quantity
                transaction.items.append(SaleItem(product=product, quantity=quantity, unit_cost=money(product.cost_price), unit_price=unit_price, line_total=line_total))
                db.session.add(InventoryMovement(product=product, movement_type="sale", quantity=-quantity, reference=transaction.bill_number, note="One-year demo sale", created_at=sale_time))
            discount_amount = money(subtotal * Decimal(random.choice(["0", "0", "0.02", "0.03"])))
            taxable_amount = subtotal - discount_amount
            transaction.subtotal = money(subtotal)
            transaction.discount_amount = discount_amount
            transaction.tax_amount = money(taxable_amount * Decimal("0.05"))
            transaction.total = money(taxable_amount + transaction.tax_amount)
            bill_count += 1

    db.session.commit()
    flash(f"Old business data deleted. Created 50 products, 10 customers, and {bill_count} bills across one year.", "success")
    return redirect(url_for("dashboard", period="last_year"))


def seed_demo_data():
    if not User.query.filter_by(username="owner").first():
        db.session.add_all(
            [
                User(username="owner", password_hash=generate_password_hash("owner123"), role="owner"),
                User(username="admin", password_hash=generate_password_hash("admin123"), role="admin"),
                User(username="cashier", password_hash=generate_password_hash("cashier123"), role="cashier"),
            ]
        )
    if not Vendor.query.first():
        db.session.add(Vendor(name="Default Vendor", gst_number="29DEFAULT1234F1Z1", state="Karnataka"))
        db.session.flush()
    if not Product.query.first():
        vendor = Vendor.query.first()
        products = [
            Product(name="Slim Fit Shirt", article_code="SH-1021", size="M", color="White", cost_price=650, selling_price=1299, stock_level=20, barcode="89010001", vendor=vendor, gst_rate=5),
            Product(name="Chino Trouser", article_code="TR-2210", size="32", color="Navy", cost_price=800, selling_price=1799, stock_level=12, barcode="89010003", vendor=vendor, gst_rate=12),
            Product(name="Denim Jacket", article_code="JK-7788", size="M", color="Blue", cost_price=1400, selling_price=2999, stock_level=8, barcode="89010004", vendor=vendor, gst_rate=12),
        ]
        db.session.add_all(products)
    if not Customer.query.filter_by(phone="0000000000").first():
        db.session.add(Customer(name="Walk-in Customer", phone="0000000000", credit_balance=0, country_code="+91", country_name="India"))
    db.session.commit()


def ensure_user_profile_columns():
    existing_columns = {row[1] for row in db.session.execute(text('PRAGMA table_info("user")')).all()}
    for name, column_type in {"full_name": "VARCHAR(120)", "employee_id": "VARCHAR(50)", "phone": "VARCHAR(30)", "email": "VARCHAR(120)"}.items():
        if name not in existing_columns:
            db.session.execute(text(f'ALTER TABLE "user" ADD COLUMN {name} {column_type}'))
    db.session.commit()


with app.app_context():
    db.create_all()
    ensure_user_profile_columns()
    seed_demo_data()


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5000")))
