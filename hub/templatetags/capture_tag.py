from django import template
from django.template.base import Node

register = template.Library()

class CaptureNode(Node):
    def __init__(self, nodelist, var_name):
        self.nodelist = nodelist
        self.var_name = var_name

    def render(self, context):
        output = self.nodelist.render(context)
        context[self.var_name] = output
        return ''

@register.tag(name='capture')
def do_capture(parser, token):
    try:
        tag_name, var_name = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError("%r tag requires a single argument" % token.contents.split()[0])
    
    nodelist = parser.parse(('endcapture',))
    parser.delete_first_token()
    
    return CaptureNode(nodelist, var_name)
