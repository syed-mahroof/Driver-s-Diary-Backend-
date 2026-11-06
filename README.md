# Driver-s-Diary-Backend-
Full-stack cab management system built with Django REST Framework and React (Vite). Features real-time ride manifests, driver vehicle preferences, PWA support, and advanced PDF reporting. Powered by PostgreSQL, SimpleJWT, and Gunicorn. Rebranded as Driver's Diary by Head Green!

## Admin workspace API

All new mutation and performance endpoints require an authenticated staff admin. Mutations write the actor and operation to Django's existing admin log. No database schema changes are required.

| Method | Route under `/api/` | Action |
| --- | --- | --- |
| POST | `admin/rides/create/` | Create a ride for a driver |
| PATCH | `admin/rides/<id>/` | Edit a ride, including driver/date reassignment |
| POST | `admin/charge/create/` | Record a charging expense |
| PATCH | `admin/charge/<id>/` | Edit charging details |
| POST | `admin/advance-salary/create/` | Record a paid advance or pending request |
| POST | `admin/drivers/create/` | Create a driver with phone or email |
| PATCH | `admin/drivers/<id>/` | Edit profile, login, default vehicle or active status |
| DELETE | `admin/drivers/<id>/` | Disable login and retain historical records |
| DELETE | `admin/rides/<id>/` | Delete a ride and its identical duplicates; attendance updates automatically |
| GET | `admin/performance/` | Driver activity, daily trend and salary-cycle metrics |

For paid advances, `paid_date` controls `resolved_date` and the salary cycle that deducts the payment. Pending requests never reduce net payable. Amounts must be positive finite decimals. Driver-supplied advance requests always remain pending until an admin resolves them.

`admin/drivers/?include_inactive=true` includes removed accounts for management and historical filtering. Admin accounts cannot be edited or removed through driver management.

Dashboard, reports and Excel accept CSV or repeated `driver_id`, `company_id` and `vehicle_number` values. Existing scalar filters remain supported. Dates use `YYYY-MM-DD`; invalid IDs, malformed dates and reversed ranges return HTTP 400. Performance accepts up to 366 days, defaults to month-to-date, and uses elapsed calendar days for average trips/day. Salary-cycle metrics include the driver's full cycle regardless of company/vehicle filters.

Reports and Excel batch data instead of querying each driver/day. Performance query count remains constant as the number of drivers increases. Existing salary rules are preserved.

## Saved driver passwords

Passwords an admin sets for a driver (in Manage drivers) are kept so a superuser can read them on the Django admin Driver page if the driver forgets. No key or setup is needed. They are stored as plain text, so anyone with database access can read them; keep superuser and database access limited. A saved password is hidden once the driver changes it, and it never appears in the API.

## Isolated verification

Install `backend/requirements.txt`, then run from `backend`:

```sh
python manage.py test cabapp --settings=cabservice.test_settings --noinput
```

The test settings use SQLite in memory and a local email backend, so verification does not contact the configured PostgreSQL database or send email. Use these settings only for verification; their fast password hasher is unsuitable for deployment.

The 21 tests cover admin permissions, driver ownership, attendance recalculation, charge edits, payment dates, invalid amounts, account removal and restoration, existing JWT rejection after removal, multi-filter Excel contents, filter validation, empty activity days, salary filter scope, fixed query counts and offline retry behavior. PostgreSQL concurrency and actual mobile browser behavior still need environment-specific checks before release.
