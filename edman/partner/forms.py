from django import forms
from .models import PartnerAccount

class LeadUploadForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=PartnerAccount.objects.none(),
        label="Partner Account"
    )
    file = forms.FileField(label="Leads CSV File")

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['account'].queryset = PartnerAccount.objects.filter(user=user)
