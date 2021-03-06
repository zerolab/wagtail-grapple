import graphene
from django.contrib.contenttypes.models import ContentType
from wagtail.core.models import Page as WagtailPage
from graphene_django.types import DjangoObjectType
from graphql.execution.base import ResolveInfo
from rx.subjects import Subject
from django.dispatch import receiver

from ..signals import preview_update
from ..registry import registry
from ..utils import resolve_queryset
from .structures import QuerySetList


class PageInterface(graphene.Interface):
    id = graphene.ID()
    url = graphene.String()
    slug = graphene.String()
    depth = graphene.Int()
    page_type = graphene.String()
    title = graphene.String()
    seo_title = graphene.String()
    seo_description = graphene.String()
    show_in_menus = graphene.Boolean()
    content_type = graphene.String()
    parent = graphene.Field(lambda: PageInterface)
    children = QuerySetList(lambda: PageInterface, enable_search=True)
    siblings = QuerySetList(lambda: PageInterface, enable_search=True)
    next_siblings = QuerySetList(lambda: PageInterface, enable_search=True)
    previous_siblings = QuerySetList(lambda: PageInterface, enable_search=True)
    descendants = QuerySetList(lambda: PageInterface, enable_search=True)
    ancestors = QuerySetList(lambda: PageInterface, enable_search=True)

    def resolve_content_type(self, info: ResolveInfo):
        self.content_type = ContentType.objects.get_for_model(self)
        return (
            self.content_type.app_label + "." + self.content_type.model_class().__name__
        )

    @classmethod
    def resolve_type(cls, instance, info: ResolveInfo):
        """
        If model has a custom Graphene Node type in registry then use it,
        otherwise use base page type.
        """
        mdl = type(instance)
        if mdl in registry.pages:
            return registry.pages[mdl]
        else:
            return Page

    def resolve_parent(self, info, **kwargs):
        """
        Resolves the parent node of current page node.
        Docs: http://docs.wagtail.io/en/v2.5.1/reference/pages/model_reference.html?highlight=get_parent#wagtail.core.models.Page.get_parent
        """
        return resolve_queryset(self.get_parent().specific(), info, **kwargs)

    def resolve_children(self, info, **kwargs):
        """
        Resolves a list of live children of this page with `show_in_menus` set.
        Docs: http://docs.wagtail.io/en/v2.5.1/reference/pages/queryset_reference.html#examples
        """
        return resolve_queryset(self.get_children().specific(), info, **kwargs)

    def resolve_siblings(self, info, **kwargs):
        """
        Resolves a list of sibling nodes to this page.
        Docs: http://docs.wagtail.io/en/v2.5.1/reference/pages/queryset_reference.html?highlight=get_siblings#wagtail.core.query.PageQuerySet.sibling_of
        """
        return resolve_queryset(
            self.get_siblings().exclude(pk=self.pk).specific(), info, **kwargs
        )

    def resolve_next_siblings(self, info, **kwargs):
        """
        Resolves a list of direct next siblings of this page. Similar to `resolve_siblings` with sorting.
        Source: https://github.com/wagtail/wagtail/blob/master/wagtail/core/models.py#L1384
        """
        return resolve_queryset(
            self.get_next_siblings().exclude(pk=self.pk).specific(), info, **kwargs
        )

    def resolve_prev_siblings(self, info, **kwargs):
        """
        Resolves a list of direct prev siblings of this page. Similar to `resolve_siblings` with sorting.
        Source: https://github.com/wagtail/wagtail/blob/master/wagtail/core/models.py#L1387
        """
        return resolve_queryset(
            self.get_prev_siblings().exclude(pk=self.pk).specific(), info, **kwargs
        )

    def resolve_ancestors(self, info, **kwargs):
        """
        Resolves a list of nodes pointing to the current page’s ancestors.
        Docs: https://docs.wagtail.io/en/v2.5.1/reference/pages/model_reference.html?highlight=get_ancestors#wagtail.core.models.Page.get_ancestors
        """
        return resolve_queryset(self.get_ancestors().specific(), info, **kwargs)

    def resolve_seo_title(self, info):
        """
        Get page's SEO title. Fallback to a normal page's title if absent.
        """
        return self.seo_title or self.title


class Page(DjangoObjectType):
    """
    Base page type used if one isn't generated for the current model.
    All other node types extend this.
    """

    class Meta:
        model = WagtailPage
        interfaces = (PageInterface,)


def get_specific_page(id, slug, token):
    """
    Get a spcecific page, also get preview if token is passed
    """
    page = None
    try:
        if id:
            page = WagtailPage.objects.live().public().specific().get(pk=id)
        if slug:
            page = WagtailPage.objects.live().public().specific().get(slug=slug)
        if token and page:
            page_type = type(page)
            page = page_type.get_page_from_preview_token(token)
    except BaseException:
        page = None

    return page


def PagesQuery():
    # Add base type to registry
    registry.pages[type(WagtailPage)] = Page

    class Mixin:
        pages = QuerySetList(lambda: PageInterface, enable_search=True)
        page = graphene.Field(
            PageInterface,
            id=graphene.Int(),
            slug=graphene.String(),
            token=graphene.String(),
        )

        # Return all pages, ideally specific.
        def resolve_pages(self, info, **kwargs):
            return resolve_queryset(
                WagtailPage.objects.live().public().specific(), info, **kwargs
            )

        # Return a specific page, identified by ID or Slug.
        def resolve_page(self, info, **kwargs):
            return get_specific_page(
                id=kwargs.get("id"), slug=kwargs.get("slug"), token=kwargs.get("token")
            )

    return Mixin


# Subject to sync Django Signals to Observable
preview_subject = Subject()


@receiver(preview_update)
def on_updated(sender, token, **kwargs):
    preview_subject.on_next(token)


# Subscription Mixin
def PagesSubscription():
    def preview_observable(id, slug, token):
        return preview_subject.filter(lambda previewToken: previewToken == token).map(
            lambda token: get_specific_page(id, slug, token)
        )

    class Mixin:
        page = graphene.Field(
            PageInterface,
            id=graphene.Int(),
            slug=graphene.String(),
            token=graphene.String(),
        )

        def resolve_page(self, info, **kwargs):
            return preview_observable(
                id=kwargs.get("id"), slug=kwargs.get("slug"), token=kwargs.get("token")
            )

    return Mixin
