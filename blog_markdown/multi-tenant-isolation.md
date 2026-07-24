---
title: "Multi-Tenant Data Isolation at the ORM Layer"
date: "2026-07-20"
summary: "How the Business Dashboard enforces per-tenant row-level security. Why controller checks alone are not enough."
pinned: false
---

# Multi-Tenant Data Isolation at the ORM Layer

## The App

Business Dashboard is a small business management tool. Something more useful than a spreadsheet but not as heavy as a full CRM. Users sign up, add or import customers, create orders, and see revenue insights from a private dashboard. It is live at businessdashboard.shop.

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

## The ORM-Level Filter

The solution in Business Dashboard was to build a base query pattern that always includes the tenant filter, and to make it the default way to query data:

```python
def get_user_customers(user_id):
    return Customer.query.filter_by(user_id=user_id).order_by(Customer.created_at.desc()).all()

def get_user_orders(user_id):
    return Order.query.filter_by(user_id=user_id).order_by(Order.created_at.desc()).all()
```

Every query goes through a service function that takes `user_id` as a required parameter. There is no "get all customers" or "get order by ID without a user" function. The tenant filter is structurally enforced by the API surface. You literally cannot query data without specifying which user owns it.

This is a deliberate design choice that trades some flexibility for correctness. If I need an admin function that crosses tenant boundaries, I write a separate function with a different name and explicit admin-only access control. The default path is always tenant-scoped.

The service layer acts as a barrier between the routes and the database. Routes call service functions, never query the ORM directly. This pattern means a new endpoint cannot accidentally skip the tenant filter because the only way to access data is through the service functions.

## Delete Protection

Another isolation edge case: deletion. Can a user delete a customer that has orders attached? In the Business Dashboard, customers and orders are explicitly linked. Every order belongs to a customer, and both belong to the same user. The delete logic enforces this:

- A customer can only be deleted if they have zero orders
- An order deletion only affects that user's data
- The `user_id` is checked on every delete operation before the query executes

This prevents cascade-based data loss and ensures that deleting a customer in User A's dashboard never touches User B's orders, even if a foreign key relationship exists between them. The frontend also reflects this: the delete button is hidden on customers that have orders.

## CSV and JSON Import with Tenant Scoping

The dashboard supports bulk importing customers and orders via CSV or JSON upload. The import service parses the file, validates each row, and inserts records, all scoped to the authenticated user.

The import flow has its own edge cases worth documenting.

**Duplicate detection.** If an imported customer has the same email as an existing customer for that user, it is skipped (not overwritten). But the same email for a different user is allowed. Tenants are independent. This means two users can both have a customer with email "john@example.com" without any conflict.

**Validation before insertion.** Every row is validated before any inserts happen. If row 47 of a 100-row CSV fails validation, none of the rows are inserted. This prevents partial imports that leave the database in an inconsistent state. The user gets a clear error message about which row failed and why.

**Type coercion.** The import service converts string values from CSV into the correct types (dates, numbers, booleans) before insertion, with clear error messages for malformed data. A CSV cell with "$1,200.50" gets converted to 1200.50 as a float. A cell with "2025-01-15" gets parsed into a date object. If the conversion fails, the import rolls back and reports the error.

## Revenue Insights from Relational Data

The dashboard page calculates metrics from the order data: total revenue, average order value, pending orders, and top customers by revenue. These queries all go through the same tenant-scoped service functions.

The key insight here is that the metrics are not cached. Every dashboard load runs fresh queries against the database. For a small business tool this is fine. The queries are simple aggregations with a WHERE clause for the user ID, and PostgreSQL handles them in milliseconds. If the dataset grew to millions of orders, I would add materialized views or a caching layer, but for the current scale, direct queries are the right choice.

## The Architecture in Practice

The Business Dashboard uses Flask with SQLAlchemy (sync) and server-rendered HTML with TailwindCSS. There is no JavaScript framework, no API layer, no separate frontend build step. The backend renders templates directly.

This is an intentional choice for this project. The app is a CRUD dashboard with forms, tables, and charts. A React frontend would add complexity without clear benefit. The server-rendered approach means the tenant filter is applied before any HTML is generated, so there is no risk of client-side code accidentally fetching cross-tenant data. The user's data is determined on the server and the template only renders what the service layer returns.

The tech stack:
- **Flask** for the web framework with blueprint modularization
- **SQLAlchemy** as the ORM with declarative models
- **Alembic** for database migrations
- **PostgreSQL** as the relational database, hosted on Render
- **TailwindCSS** via CDN for utility-first styling

## What I Would Do Differently

**Row-level security (RLS) at the database level.** PostgreSQL supports RLS policies that enforce tenant isolation at the query planner level, before the query even executes. This is the gold standard for multi-tenant isolation. For this project, ORM-level filtering was sufficient, but for a production SaaS with multiple developers, I would add RLS as a safety net.

**Soft deletes.** Currently, deleting a customer or order is permanent. In a real business tool, soft deletes (marking records as inactive) would prevent accidental data loss and allow audit trails. The current approach uses hard deletes with a check for existing orders before allowing customer deletion.

**Request-scoped session injection.** Instead of passing `user_id` to every service function, I would inject it via Flask's `g` object or a dependency injection pattern. This reduces boilerplate and makes it impossible to forget. Every request already has the authenticated user loaded into the session, so passing it through `g` would be a small architectural change with a large safety benefit.

## The Takeaway

Multi-tenant isolation is not a feature you add. It is a constraint you design around. The question is not "where do I add the user filter?" but "how do I make it impossible to query without one?" In Business Dashboard, the answer was to put the filter at the service layer and never expose unscoped query functions. It is not the most sophisticated approach, but it is correct, auditable, and has no known data leaks.

---

*Live at [businessdashboard.shop](https://businessdashboard.shop) · Code at [github.com/RichardOyelowo/Business-Dashboard](https://github.com/RichardOyelowo/Business-Dashboard)*
