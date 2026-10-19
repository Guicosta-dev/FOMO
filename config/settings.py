"""Configurações do projeto FOMO.

Valores sensíveis vêm de variáveis de ambiente (arquivo .env local).
"""
import os
from pathlib import Path

from django.contrib.messages import constants as messages_constants
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Lê o arquivo .env (se existir) e o transforma em variáveis de ambiente.
load_dotenv(BASE_DIR / ".env")

DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "inseguro-use-apenas-em-desenvolvimento"
    else:
        raise ImproperlyConfigured(
            "Defina DJANGO_SECRET_KEY no arquivo .env (veja .env.example)."
        )

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # App da FOMO (contas, eventos e painel ficam todos aqui)
    "app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
                     "context_processors": [
                                             "django.template.context_processors.request",
                                             "django.contrib.auth.context_processors.auth",
                                             "django.contrib.messages.context_processors.messages",
                                            "app.context_processors.branding",
                    ],
   
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Banco de dados: SQLite no desenvolvimento local (decisão em aberto: PostgreSQL depois).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Usuário customizado: precisa ser definido ANTES da primeira migração.
AUTH_USER_MODEL = "app.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "app:login"
LOGOUT_REDIRECT_URL = "app:catalog"

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Faz as mensagens de erro usarem a classe "alert-danger" do Bootstrap.
MESSAGE_TAGS = {messages_constants.ERROR: "danger"}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
