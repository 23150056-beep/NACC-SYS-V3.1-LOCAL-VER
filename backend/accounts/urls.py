from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from accounts.views import (
    LoginView, MeView, ChangePasswordView, UserViewSet, RoleListView, PsychologistListView,
    GoogleLoginView, EmailConfigTestView, GoogleAuthConfigView, SignupView,
    MyProfileView, MyPhoneView, SmsConfigTestView,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/signup/", SignupView.as_view(), name="signup"),
    # Google Sign-In (staff and psychologists only — see accounts/google_auth.py)
    path("auth/google/", GoogleLoginView.as_view(), name="google-login"),
    path("auth/google/config/", GoogleAuthConfigView.as_view(), name="google-config"),
    path("sms-test/", SmsConfigTestView.as_view(), name="sms-test"),
    path("email-test/", EmailConfigTestView.as_view(), name="email-test"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/me/", MeView.as_view(), name="me"),
    # Your own optional details. No id in the path on purpose — see the view.
    path("auth/me/profile/", MyProfileView.as_view(), name="my-profile"),
    # Your own mobile number and its verification. No id, same rule.
    path("auth/me/phone/", MyPhoneView.as_view(), name="my-phone"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("roles/", RoleListView.as_view(), name="role-list"),
    path("psychologists/", PsychologistListView.as_view(), name="psychologists"),
    path("", include(router.urls)),
]
