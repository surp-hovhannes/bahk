from datetime import datetime

def current_date(request):
    return {'current_date': datetime.today()}