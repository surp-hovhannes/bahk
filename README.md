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

To set up the database, migrate, populate with seed data, and create a superuser for yourself.
To do this, navigate to the root directory (`bahk/`) and enter the following in the terminal:
```
python manage.py migrate
python manage.py seed
python manage.py createsuperuser
```
Note the username, email, and password that you enter.

Now you're ready to run the app! In the terminal, enter
```
python manage.py runserver
```
This command runs `bahk` on your local server at port 8000 (you can specify your own port with the `--port <port>` flag).

Access the app at http://localhost:8000

### Testing the Site

By populating the database with the seed data, you can login with any of 4 accounts: usernames `user1`, `user2`,
`user3`, or `user4`. The password is `default123` for each.

Users `user1a` and `user1b` are part of `Church1` and are participating in `Fast1`. User `user2` is part of `Church2`
and is participating in `Fast2`. User `user3` is part of `Church3` but is not participating in any fasts.
These data will appear on the home page once you log in with one of the users. You
should also see the number of participants in the fast (including the user).

Try joining a fast! Log in as `user3` and select `Fast3` from the menu (it should be the only option). Then click
[Home](http://localhost:8000/hub/?next=/hub/) on the sidebar to go back to the home page. It should now show that
you are fasting `Fast3` with one faithful (you).

## Contact Us!

Bahk is maintained by Dn. Andrew Ylitalo and Fr. Mesrop Ash of [St. John Armenian Church](https://stjohnarmenianchurch.com/)
in San Francisco, CA. Feel free to reach out with questions at ylitaand@gmail.com.
