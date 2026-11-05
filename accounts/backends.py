from django.contrib.auth.backends import ModelBackend


class ApprovedUserBackend(ModelBackend):
    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user) or not user.is_approved:
            return False
        employee = getattr(user, "employee_profile", None)
        return employee is None or employee.is_active
