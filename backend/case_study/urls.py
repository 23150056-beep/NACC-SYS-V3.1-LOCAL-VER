from django.urls import path

from case_study.views import (
    CaseStudyView, FinalCopyView, FinalView, ReopenView, SectionView)

urlpatterns = [
    path("child/<int:child_id>/", CaseStudyView.as_view(), name="case-study"),
    path("child/<int:child_id>/sections/<str:key>/", SectionView.as_view(),
         name="case-study-section"),
    path("child/<int:child_id>/final/", FinalView.as_view(), name="case-study-final"),
    path("child/<int:child_id>/reopen/", ReopenView.as_view(), name="case-study-reopen"),
    path("child/<int:child_id>/finals/<int:final_id>/", FinalCopyView.as_view(),
         name="case-study-final-copy"),
]
