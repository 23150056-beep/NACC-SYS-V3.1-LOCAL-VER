from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from accounts.models import Role, User


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    """Role rows come from migrations only and are never renamed: the code
    decides what each name may do (accounts/capabilities.py), so a row added
    or renamed here would be an account the code does not know."""
    list_display = ("role_name",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(User, UserAdmin)
