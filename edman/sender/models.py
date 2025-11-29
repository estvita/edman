from django.db import models
from edman.hh.models import Employer
from django.contrib.auth import get_user_model

User = get_user_model()

# Create your models here.
class Sender(models.Model):
    owner = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL)
    employers = models.ManyToManyField(Employer, related_name="senders")
    type = models.CharField(max_length=50)
    uri = models.URLField()
    key = models.CharField(max_length=500)
    text = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.type} {self.id}"