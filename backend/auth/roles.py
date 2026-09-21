from enum import Enum

class UserRole(str, Enum):
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    HOSPITAL_ADMIN = "HOSPITAL_ADMIN"
    DOCTOR = "DOCTOR"
    PATIENT = "PATIENT"

# Permission definitions for Role-Based Access Control (RBAC)
ROLE_PERMISSIONS = {
    UserRole.PLATFORM_ADMIN: [
        "hospitals:approve",
        "hospitals:manage",
        "platform:audit",
        "platform:metrics",
        "ehr:configure_chaos"
    ],
    UserRole.HOSPITAL_ADMIN: [
        "hospital:manage_own",
        "doctors:create",
        "doctors:manage_own",
        "schedules:configure_own",
        "questionnaires:configure_own"
    ],
    UserRole.DOCTOR: [
        "availability:manage_own",
        "appointments:view_own",
        "previsit:view_own"
    ],
    UserRole.PATIENT: [
        "appointments:book_own",
        "appointments:view_own",
        "appointments:cancel_own",
        "questionnaires:submit_own"
    ]
}
