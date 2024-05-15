from django.contrib.auth.forms import UserCreationForm 
from django import forms
from django.contrib.auth.models import User

from hub.models import Church, Fast, Profile


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    church = forms.ChoiceField(label="Churches", choices=[])
    
    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['church'].choices = [(church.name, church.name) for church in Church.objects.all()]


class JoinFastsForm(forms.Form):
    fasts = forms.ModelMultipleChoiceField(queryset=Fast.objects.none(), widget=forms.SelectMultiple)
    
    def __init__(self, *args, **kwargs):
        request = kwargs.pop("request", None)  # Extract the request object
        super().__init__(*args, **kwargs)
        if request is not None:
            self.fields["fasts"].queryset = Fast.objects.filter(church=request.user.profile.church)

class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['church']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['church'].widget.attrs.update({'class': 'form-control'})