from rest_framework.renderers import BrowsableAPIRenderer


class ChurchListBrowsableAPIRenderer(BrowsableAPIRenderer):
    def get_filter_form(self, data, view, request):
        # DRF otherwise calls get_queryset even when there are no filter controls,
        # repeating church validation outside the view's exception handling.
        if not view.filter_backends:
            return None
        return super().get_filter_form(data, view, request)
