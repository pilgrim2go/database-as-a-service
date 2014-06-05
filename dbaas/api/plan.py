# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
from rest_framework import viewsets, serializers
from physical import models
from .environment import EnvironmentSerializer


class PlanSerializer(serializers.HyperlinkedModelSerializer):

    environments = EnvironmentSerializer(many=True, read_only=True)

    class Meta:
        model = models.Plan
        fields = ('url', 'id', 'name', 'description', 'is_active', 'is_default', 'engine_type', 'environments',)


class PlanAPI(viewsets.ReadOnlyModelViewSet):
    """
    Plan API
    """
    serializer_class = PlanSerializer
    queryset = models.Plan.objects.all()

    
    def get_queryset(self):
        """
        Optionally restricts the returned purchases to a given user,
        by filtering against a `username` query parameter in the URL.
        """
        queryset = models.Plan.objects.all()
        engine_id = self.request.QUERY_PARAMS.get('engine_id', None)
        try:
            if engine_id is not None:
                queryset = models.Plan.objects.filter(engine_type=models.Engine.objects.get(id=engine_id).engine_type)
        except:
            pass
    
        return queryset
