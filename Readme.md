# Leave Management System (LMS)

A web-based Leave Management System built with **Django** and **REST APIs** to manage employee leaves, roles, and notifications efficiently. This system allows admins and team leads to assign tasks, approve leaves, and track employee performance.

---

## Features

- **User Management**
  - Admin, Team Lead, and Employee roles
  - Role-based permissions
  - JWT Authentication for secure login

- **Leave Management**
  - Apply, approve, or reject leave requests
  - Track leave balances
  - Automatic notifications to relevant users

- **Task Management**
  - Assign tasks to employees or teams
  - Notification system for updates

- **Notifications**
  - Email and in-app notifications for leave requests and task updates

- **Dashboard**
  - Performance metrics
  - Leave summary
  - Task overview

---

## Tech Stack

- **Backend:** Django, Django REST Framework
- **Frontend:** HTML, CSS, JavaScript, Bootstrap
- **Database:** SQLite/PostgreSQL
- **Authentication:** JWT
- **Notifications:** Email (SMTP), Twilio (SMS), Slack integration
- **Deployment:** Gunicorn + Nginx / Docker

---

## Installation

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/lms.git
cd lms

2. **Create virtual environment**
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows

3. **Install dependencies**
pip install -r requirements.txt

4. **Run migrations**
python manage.py makemigrations
python manage.py migrate

5. **Create superuser**
python manage.py createsuperuser

6. **Run the server**
python manage.py runserver

7. **API Endpoints**
| Endpoint           | Method      | Description                    |
| ------------------ | ----------- | ------------------------------ |
| `/api/login/`      | POST        | Login and receive JWT token    |
| `/api/leave/`      | GET, POST   | List or create leave requests  |
| `/api/leave/<id>/` | PUT, DELETE | Update or delete leave request |
| `/api/tasks/`      | GET, POST   | List or create tasks           |
| `/api/tasks/<id>/` | PUT, DELETE | Update or delete task          |
