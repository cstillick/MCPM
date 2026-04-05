from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
# Expose CSRF cookie value to all templates as csrf_token(request)
templates.env.globals["csrf_token"] = lambda req: req.cookies.get("csrftoken", "")
