# Bahk

Bahk is an app for Christians curious about how, when, and why to fast.

## Prerequisites

Bahk runs on [Django](https://www.djangoproject.com/), a "high-level Python web framework". To operate the app, 
download the [latest version of Python](https://www.python.org/downloads/)
(Linux distros will come with Python--check using `python --version`).

*Note*: these instructions are written for Linux and Mac users. Windows development is not currently supported. For
Windows users interested in using the app, we recommend installing the [Windows Subsystem for Linux (WSL)](https://learn.microsoft.com/en-us/windows/wsl/install).

## Getting Started

### Setting Up the Repo

Begin by cloning the repo:
```
git clone https://github.com/andylitalo/bahk.git
```

We recommend separating this project's packages into their own Python virtual environment. 
If you do not have the `virtualenv` package in your Python installation, install it with 
`python -m pip install virtualenv`.
Then, enter the project's root directory and create a virtual environment:
```
cd bahk
python -m venv venv
```

To activate the virtual environment, run `source venv/bin/activate`. Now, you are ready to install the requirements:
```
pip install -r requirements.txt
```

### Setting Up the Database

First create a superuser for yourself. This account will allow you to access all aspects
of the app.
Navigate to the root directory (`bahk/`) and enter the following in the terminal:
```
python manage.py migrate
python manage.py createsuperuser
```
Note the username, email, and password that you enter.

Now you're ready to run the app! In the terminal, enter
```
python manage.py runserver
```
This command runs `bahk` on your local server at port 8000 (you can specify your own port with the `--port <port>` flag).

Access the app at http://localhost:8000

To navigate through the app, click "Login" in the upper right and enter the credentials of the superuser account 
you created.

### Sample Endpoints

Access the admin page at http://localhost:8000/admin/.
Here, you can browse and modify the database using Django's admin interface. Go see your user under the "User"
link on the side panel!

You can also retrieve data like a client through API endpoints on the "hub" app. Try
http://localhost:8000/hub/users/.
If logged in as an admin user, you should see a list of users with URL, username, email, and groups that they belong to.

### Endpoints to Test

To test the current endpoints, go to the [admin page](http://localhost:8000/admin/) and add the following:
* Church: create a church and give it a name
* Fast: create two fasts with names and assign them to your church
* Users: create two users (no special groups or permissions)
* Profiles: create a profile for each user, assigning them to your church and adding the fasts that you want each 
user to participate in
* Days: create a day for each day in the duration of each of your fasts and assign them to the appropriate fasts

We are currently testing one endpoint:

1. `http://localhost:8000/hub/fast/`: returns a dictionary with data for the logged-in user's fast on a given date.
The date is passed in as a query parameter, *e.g.*, to get the fast on March 29, 2024, append `?date=20240329` to the 
URL. If no date is passed, defaults to today. An invalid date string will return an empty fast. The dictionary should
contain the following:
    * the name of the fast
    * the church to which the fast belongs
    * the number of participants in the fast

## Contact Us!

Bahk is maintained by Dn. Andrew Ylitalo and Fr. Mesrop Ash of [St. John Armenian Church](https://stjohnarmenianchurch.com/)
in San Francisco, CA. Feel free to reach out with questions at ylitaand@gmail.com.
