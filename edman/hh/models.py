from django.db import models
from django.utils import timezone
from django.contrib.sites.models import Site
from django.contrib.auth import get_user_model

User = get_user_model()

class App(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='hh_apps')
    redirect_uri = models.URLField()
    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    

class Employer(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    app = models.ForeignKey(App, on_delete=models.CASCADE)
    user_id = models.CharField(max_length=255)
    user_email = models.EmailField()
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    subscription = models.PositiveIntegerField(null=True)

    def __str__(self):
        return f"{self.user_id} {self.user_email}"
    

class Resume(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="resumes")
    last_name = models.CharField(max_length=255, null=True, blank=True)
    first_name = models.CharField(max_length=255, null=True, blank=True)
    title = models.CharField(max_length=255, null=True, blank=True)
    date = models.DateTimeField(default=timezone.now)
    raw_json = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"{self.id} {self.last_name}"


class Contact(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="contacts")
    type = models.CharField(max_length=50)
    value = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.resume}: {self.value}"