from django.urls import path

from case_study.views import CaseStudyView, SectionView

urlpatterns = [
    path("child/<int:child_id>/", CaseStudyView.as_view(), name="case-study"),
    path("child/<int:child_id>/sections/<str:key>/", SectionView.as_view(),
         name="case-study-section"),
]
