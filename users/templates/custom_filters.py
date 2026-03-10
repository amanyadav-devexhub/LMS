# yourapp/templatetags/custom_filters.py
#
# 1. Create folder:   yourapp/templatetags/
# 2. Create file:     yourapp/templatetags/__init__.py  (empty)
# 3. Add this file:   yourapp/templatetags/custom_filters.py
# 4. Load in template: {% load custom_filters %}

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """
    Allows dict lookup by variable key in Django templates.
    Usage: {{ my_dict|get_item:some_variable }}
    """
    if dictionary is None:
        return None
    return dictionary.get(key)