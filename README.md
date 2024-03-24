# Bahk

Bahk is an app for Christians curious about how, when, and why to fast.

## Prerequisites

Bahk runs on [Django](https://www.djangoproject.com/), a "high-level Python web framework". To operate the app, 
download the [latest version of Python](https://www.python.org/downloads/)
(Linux distros will come with Python--check using `python --version`).

## Getting Started

After cloning this repo, first create a superuser for yourself. This account will allow you to access all aspects
of the app.
Navigate to the root directory (`bahk/`) and enter the following in the terminal:
```
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
You should see a list of users with URL, username, email, and groups that they belong to.

### Endpoints to Test

To test the current endpoints, go to the [admin page](http://localhost:8000/admin/) and add the following:
* Church: create a church and give it a name
* Fast: create two fasts with names and assign them to your church
* Users: create two users (no special groups or permissions)
* Profiles: create a profile for each user, assigning them to your church and adding the fasts that you want each 
user to participate in
* Days: create a day for each day in the duration of each of your fasts and assign them to the appropriate fasts

We are currently testing four endpoints:

1. `http://localhost:8000/hub/fast/today/`: returns the name of the fast your user is participating in
today (or `"None"` if your user is not participating in a fast).
2. `http://localhost:8000/hub/fast/today/participant_count/`: returns the number of
people participating in your user's fast today 
3. `http://localhost:8000/hub/fast/<yyyymmdd>/`: returns the name of the fast your user is participating in
on the specified date `<yyyymmdd>` (e.g., the fast your user is participating in on March 29, 2024 would be found
at `http://localhost:8000/hub/fast/20240329/`)
4. `http://localhost:8000/hub/fast/<yyyymmdd>/participant_count/`: returns the number of people participating
in your user's fast on the given date `<yyyymmdd>`

## Contact Us!

Bahk is maintained by Dn. Andrew Ylitalo and Fr. Mesrop Ash of St. John Armenian Church in San Francisco, CA.
Feel free to reach out with questions at ylitaand@gmail.com.
