from rest_framework import generics, permissions
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.serializers import RegisterSerializer, UserSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    # Public endpoint: a stale Authorization header must not turn into a 401.
    authentication_classes = []
    permission_classes = [permissions.AllowAny]


class LoginView(TokenObtainPairView):
    pass


class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user
