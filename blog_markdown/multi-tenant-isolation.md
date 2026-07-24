---
title: "How I Prevented Cross-Tenant Data Leaks in a Flask App"
date: "2025-07-20"
summary: "How the Business Dashboard enforces per-tenant data isolation at the ORM layer. Why controller checks alone are not enough."
pinned: false
---

# How I Prevented Cross-Tenant Data Leaks in a Flask App

## The App

Business Dashboard is a small business management tool. Something more useful than a spreadsheet but not as heavy as a full CRM. Users sign up, add customers, create orders, and see revenue insights from a private dashboard. It is live at businessdashboard.shop.

The core requirement: each user only ever sees their own data. No user can access another user's customers, orders, or revenue numbers. This sounds simple, but getting it right is where most multi-tenant apps fail.

## Why Controller Checks Are Not Enough

The obvious approach is to check the current user at the controller level:

```python
@app.route('/customers')
@login_required
def list_customers():
    customers = Customer.query.filter_by(user_id=current_user.id).all()
    return render_template('customers.html', customers=customers)
```

This works for the happy path. But it has a fundamental problem: it depends on every developer remembering to add the filter on every query. If someone adds a new endpoint and forgets the `.filter_by(user_id=...)` clause, or if a raw SQL query slips in, or if a relationship join pulls in data from another tenant, you have a data leak. There is no safety net.

I learned this the hard way during development. I had a helper function that fetched an order by ID for the edit page:

```python
order = Order.query.get(order_id)
```

No tenant filter. It worked fine in development because I was the only user. But in production with multiple users, any authenticated user could type in any order ID and see someone else's data. The fix was straightforward (add the user filter), but the fact that it was possible to forget it told me I needed a more robust approach.

## Why ORM-Level Isolation Matters

Authentication answers "who is this user?" Authorization answers "what can this user access?"

In a multi-tenant application, authorization is not just about checking permissions. It is about ensuring every database query is constrained by ownership. A perfectly authenticated request can still leak data if the query behind it is wrong.

The dangerous cases are usually not the obvious routes. They are the ones added later:

- a new admin page
- a background job
- an export feature
- a reporting query

The more places that tenant filtering depends on developer memory, the larger the chance of an isolation bug.

## The Model That Enforced the Pattern

The data model ended up being the biggest factor in how isolation works. The `Customer` model has a `user_id` foreign key, but `Order` does not. Orders belong to a customer, and customers belong to a user:

```python
class Customer(db.Model):
    __tablename__ = 'customer'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # ...
    orders = db.relationship('Order', backref='customer', lazy='dynamic')

class Order(db.Model):
    __tablename__ = 'order'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    # no user_id — isolation goes through Customer
```

Because orders lack a direct `user_id`, every order query must join through `Customer` to get the tenant. This was not an afterthought. It means the application has one ownership path from an order to a user: through the customer relationship. Every order query must follow that path.

This is what every order query in the app looks like:

```python
@orders_bp.route("/")
@login_required
def orders():
    user = g.user
    pagination = (
        Order.query.join(Customer)
        .filter(Customer.user_id == user.id)
        .order_by(Order.created.desc())
        .paginate(page=page, per_page=10, error_out=False)
    )
    return render_template("orders.html", orders=pagination.items, pagination=pagination)
```

Every route follows this pattern. Edit, delete, list. There is no "get order by ID without a user" endpoint anywhere in the codebase. The customer routes are the same idea but simpler because `Customer` has `user_id` directly, so it is just `.filter_by(user_id=user.id)`.

## Why Orders Do Not Have user_id

One possible design would be adding a user_id column directly to every tenant-owned table. That makes queries simpler:

```python
Order.query.filter_by(user_id=user.id)
```

The tradeoff is duplicated ownership data. Now every order has two relationships to maintain:

Order -> Customer -> User
Order -> User

Those can drift if something goes wrong.

For this application, an order's ownership is derived from its customer. Keeping one source of ownership truth reduces the number of consistency rules the database has to maintain.

The downside is that queries require joins. That is a tradeoff I accepted because correctness was more important than the convenience of simpler queries.

## The Global Email Trade-off

One consequence of this model: the `Customer.email` column has `unique=True` at the database level. This means no two customers in the entire system can share an email, even if they belong to different users. In a true multi-tenant system you would scope uniqueness to the tenant with a composite constraint on `(email, user_id)`. For this project the global constraint works because the tool serves small businesses where each customer email is typically unique to one business. But it is a trade-off worth knowing about.

## What I Would Do Differently

**Row-level security at the database level.** PostgreSQL supports RLS policies that enforce tenant isolation at the query planner level, before the query even executes. For this project, ORM-level filtering was sufficient, but for a production SaaS with multiple developers, I would add RLS as a safety net beneath the application layer.

**Per-tenant email uniqueness.** A composite unique constraint on `(email, user_id)` would let two different users each have a customer with the same email. The current global constraint works for the use case but is not correct for true multi-tenancy.

**Request-scoped query injection.** Right now every route explicitly references `g.user.id` in each query. A SQLAlchemy event or custom query class that automatically appends the tenant filter would make it structurally impossible to write an unscoped query, rather than relying on developer discipline.

**Soft deletes.** Currently, deleting a customer or order is permanent. Hard deletes with a pre-check for existing orders works, but soft deletes would prevent accidental data loss and allow audit trails.

## The Takeaway

Multi-tenant isolation is not a feature you add. It is a constraint you design around. The question is not "where do I add the user filter?" but "how do I make it impossible to query without one?" In Business Dashboard, the answer was to structure the data model so that orders can only be accessed through their owning customer, and to put the filter in every route query. It is not the most sophisticated approach, but it is correct, auditable, and has no known data leaks.

---

*Live at [businessdashboard.shop](https://businessdashboard.shop) · Code at [github.com/RichardOyelowo/Business-Dashboard](https://github.com/RichardOyelowo/Business-Dashboard)*
