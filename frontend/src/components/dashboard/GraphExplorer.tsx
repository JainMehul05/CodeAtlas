'use client';

import { useState, useEffect, useCallback } from 'react';
import { useAppContext } from '@/hooks/useAppContext';
import { EmptyState } from '@/components/shared/EmptyState';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { 
  GitGraph, 
  GitBranch, 
  GitCommitHorizontal, 
  FileCode, 
  Lightbulb, 
  Search, 
  ChevronRight,
  Loader2,
  AlertCircle,
  Info,
} from 'lucide-react';

interface GraphEntity {
  entityId: string;
  entityType: string;
  projectId: string;
  repositoryId?: string;
  title?: string;
  summary?: string;
  feature?: string;
  decision?: string;
  sha?: string;
  number?: number;
  path?: string;
  author?: string;
  createdAt?: string;
  updatedAt?: string;
  commitHash?: string;
  [key: string]: unknown;
}

interface GraphRelationship {
  relationshipId: string;
  relationshipType: string;
  sourceEntityId: string;
  sourceEntityType: string;
  targetEntityId: string;
  targetEntityType: string;
  projectId: string;
  repositoryId?: string;
  provenance?: string;
  confidence?: number;
  evidence?: string;
  createdAt?: string;
  [key: string]: unknown;
}

interface GraphPath {
  entityId: string;
  entityType: string;
  path: string[];
  depth: number;
  direction?: string;
  relationshipType?: string;
  provenance?: string;
  confidence?: number;
}

interface GraphResponse {
  entities: GraphEntity[];
  relationships: GraphRelationship[];
  paths: GraphPath[];
  count: number;
}

interface GraphSummaryResponse {
  summary: {
    projectId: string;
    repositoryId?: string;
    totalEntities: number;
    totalRelationships: number;
    graphDensity: number;
  };
  entityCounts: Record<string, number>;
  relationshipCounts: Record<string, number>;
  recentActivity: GraphEntity[];
}

const ENTITY_TYPE_LABELS: Record<string, string> = {
  repository: 'Repository',
  commit: 'Commit',
  pull_request: 'Pull Request',
  file: 'File',
  engineering_decision: 'Decision',
};

const RELATIONSHIP_TYPE_LABELS: Record<string, string> = {
  contains: 'Contains',
  authored_in: 'Authored In',
  modifies: 'Modifies',
  has_parent: 'Has Parent',
  includes_commit: 'Includes Commit',
  targets_file: 'Targets File',
  has_decision: 'Has Decision',
  relates_to: 'Relates To',
  derived_from: 'Derived From',
  associated_with: 'Associated With',
};

type Props = {
  projectId?: string;
  token?: string;
};

function GraphExplorerContent({
  projectId,
  token,
}: Props) {
  const [activeTab, setActiveTab] = useState<'explore' | 'summary' | 'search'>('explore');
  const [selectedEntityType, setSelectedEntityType] = useState<string>('commit');
  const [entityId, setEntityId] = useState<string>('');
  const [relationshipTypes, setRelationshipTypes] = useState<string[]>([]);
  const [direction, setDirection] = useState<'outgoing' | 'incoming' | 'both'>('both');
  const [maxDepth, setMaxDepth] = useState(2);
  const [maxResults, setMaxResults] = useState(20);
  
  const [graphData, setGraphData] = useState<GraphResponse | null>(null);
  const [summaryData, setSummaryData] = useState<GraphSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedEntity, setSelectedEntity] = useState<GraphEntity | null>(null);
  const [entityDetails, setEntityDetails] = useState<{ entities: GraphEntity[]; relationships: GraphRelationship[] } | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'https://86tzell2w9.execute-api.us-east-1.amazonaws.com/prod';

  useEffect(() => {
    if (projectId && token) {
      loadSummary();
    }
  }, [projectId, token]);

  const loadSummary = useCallback(async () => {
    if (!projectId || !token) return;
    
    try {
      const res = await fetch(`${apiUrl}/mcp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool: 'get_repository_graph_summary',
          params: { projectId },
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setSummaryData(data);
      }
    } catch (err) {
      console.error('Failed to load graph summary:', err);
    }
  }, [projectId, token, apiUrl]);

  const loadGraph = useCallback(async () => {
    if (!projectId || !token || !entityId) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const res = await fetch(`${apiUrl}/mcp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool: 'query_knowledge_graph',
          params: {
            projectId,
            entityId,
            relationshipTypes: relationshipTypes.length > 0 ? relationshipTypes : undefined,
            direction,
            maxDepth,
            maxResults,
          },
        }),
      });
      
      if (res.ok) {
        const data = await res.json();
        setGraphData(data);
      } else {
        const err = await res.json();
        setError(err.message || 'Failed to load graph');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load graph');
    } finally {
      setLoading(false);
    }
  }, [projectId, token, entityId, relationshipTypes, direction, maxDepth, maxResults, apiUrl]);

  const loadEntityDetails = useCallback(async (eid: string) => {
    if (!projectId || !token) return;
    
    setDetailsLoading(true);
    try {
      const res = await fetch(`${apiUrl}/mcp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool: 'get_related_changes',
          params: {
            projectId,
            entityType: selectedEntityType,
            entityId: eid,
            includeGraph: true,
          },
        }),
      });
      
      if (res.ok) {
        const data = await res.json();
        setEntityDetails(data);
      }
    } catch (err) {
      console.error('Failed to load entity details:', err);
    } finally {
      setDetailsLoading(false);
    }
  }, [projectId, token, selectedEntityType, apiUrl]);

  const handleEntityClick = (entity: GraphEntity) => {
    setSelectedEntity(entity);
    loadEntityDetails(entity.entityId);
  };

  const getEntityDisplayName = (entity: GraphEntity): string => {
    if (entity.title) return entity.title;
    if (entity.feature) return entity.feature;
    if (entity.sha) return `Commit ${entity.sha.slice(0, 8)}`;
    if (entity.number) return `PR #${entity.number}`;
    if (entity.path) return entity.path;
    return entity.entityId.slice(0, 20);
  };

  const getEntityTypeIcon = (type: string) => {
    switch (type) {
      case 'repository': return <GitBranch className="h-4 w-4" />;
      case 'commit': return <GitCommitHorizontal className="h-4 w-4" />;
      case 'pull_request': return <GitBranch className="h-4 w-4" />;
      case 'file': return <FileCode className="h-4 w-4" />;
      case 'engineering_decision': return <Lightbulb className="h-4 w-4" />;
      default: return <GitGraph className="h-4 w-4" />;
    }
  };

  const getEntityTypeBadge = (type: string) => {
    const variants: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
      repository: 'default',
      commit: 'secondary',
      pull_request: 'outline',
      file: 'default',
      engineering_decision: 'destructive',
    };
    return (
      <Badge variant={variants[type] || 'default'} className="gap-1">
        {getEntityTypeIcon(type)}
        {ENTITY_TYPE_LABELS[type] || type}
      </Badge>
    );
  };

  return (
    <div className="space-y-6">
      {/* Tab Navigation */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="explore">Explore Graph</TabsTrigger>
          <TabsTrigger value="summary">Repository Summary</TabsTrigger>
          <TabsTrigger value="search">Search Context</TabsTrigger>
        </TabsList>

        {/* Explore Tab */}
        <TabsContent value="explore" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Query Knowledge Graph</CardTitle>
              <Badge variant="secondary" className="text-xs">Phase 3</Badge>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <div>
                  <label className="text-sm font-medium mb-1 block">Entity Type</label>
                  <Select value={selectedEntityType} onValueChange={setSelectedEntityType}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select entity type" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="repository">Repository</SelectItem>
                      <SelectItem value="commit">Commit</SelectItem>
                      <SelectItem value="pull_request">Pull Request</SelectItem>
                      <SelectItem value="file">File</SelectItem>
                      <SelectItem value="engineering_decision">Engineering Decision</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-sm font-medium mb-1 block">Entity ID</label>
                  <Input
                    placeholder="e.g., commit:repo:...:sha"
                    value={entityId}
                    onChange={(e) => setEntityId(e.target.value)}
                  />
                </div>
                <div>
                  <label className="text-sm font-medium mb-1 block">Max Depth</label>
                  <Select value={maxDepth.toString()} onValueChange={(e) => setMaxDepth(parseInt(e))}>
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="1">1</SelectItem>
                      <SelectItem value="2">2</SelectItem>
                      <SelectItem value="3">3</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-sm font-medium mb-1 block">Max Results</label>
                  <Select value={maxResults.toString()} onValueChange={(e) => setMaxResults(parseInt(e))}>
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="10">10</SelectItem>
                      <SelectItem value="20">20</SelectItem>
                      <SelectItem value="50">50</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="text-sm font-medium mb-1 block">Relationship Types</label>
                  <div className="flex flex-wrap gap-2">
                    {Object.keys(RELATIONSHIP_TYPE_LABELS).map((type) => (
                      <label key={type} className="inline-flex items-center gap-1 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={relationshipTypes.includes(type)}
                          onChange={(e) => setRelationshipTypes(
                            e.target.checked 
                              ? [...relationshipTypes, type] 
                              : relationshipTypes.filter(t => t !== type)
                          )}
                          className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                        />
                        <span className="text-sm">{RELATIONSHIP_TYPE_LABELS[type]}</span>
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="text-sm font-medium mb-1 block">Direction</label>
                  <Select value={direction} onValueChange={(e) => setDirection(e as 'outgoing' | 'incoming' | 'both')}>
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="both">Both</SelectItem>
                      <SelectItem value="outgoing">Outgoing</SelectItem>
                      <SelectItem value="incoming">Incoming</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <Button 
                onClick={loadGraph} 
                disabled={loading || !entityId}
                className="w-full"
              >
                {loading ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Loading...
                  </>
                ) : (
                  <>
                    <Search className="mr-2 h-4 w-4" />
                    Explore Graph
                  </>
                )}
              </Button>

              {error && (
                <div className="flex items-center gap-2 text-sm text-red-600">
                  <AlertCircle className="h-4 w-4" />
                  <span>{error}</span>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Graph Results */}
          {graphData && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <GitGraph className="h-5 w-5" />
                  Graph Results
                  <Badge variant="secondary">{graphData.count} entities</Badge>
                  <Badge variant="outline">{graphData.relationships.length} relationships</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <Tabs defaultValue="entities" className="w-full">
                  <TabsList className="grid w-full grid-cols-3">
                    <TabsTrigger value="entities">Entities ({graphData.entities.length})</TabsTrigger>
                    <TabsTrigger value="relationships">Relationships ({graphData.relationships.length})</TabsTrigger>
                    <TabsTrigger value="paths">Paths ({graphData.paths.length})</TabsTrigger>
                  </TabsList>

                  <TabsContent value="entities">
                    <ScrollArea className="h-[400px]">
                      <div className="space-y-2">
                        {graphData.entities.map((entity) => (
                          <Button
                            key={entity.entityId}
                            variant="outline"
                            className="w-full justify-start text-left gap-3 p-3 hover:bg-muted/50 transition-colors"
                            onClick={() => handleEntityClick(entity)}
                          >
                            <div className="flex items-center gap-3 flex-1 min-w-0">
                              <div className="p-1.5 bg-muted rounded">
                                {getEntityTypeIcon(entity.entityType)}
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="font-medium truncate">{getEntityDisplayName(entity)}</div>
                                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                  {getEntityTypeBadge(entity.entityType)}
                                  {entity.commitHash && (
                                    <span className="font-mono">{entity.commitHash.slice(0, 12)}</span>
                                  )}
                                  {entity.author && <span>by {entity.author}</span>}
                                </div>
                              </div>
                              <ChevronRight className="h-4 w-4 text-muted-foreground" />
                            </div>
                          </Button>
                        ))}
                        {graphData.entities.length === 0 && (
                          <EmptyState
                            icon={<GitGraph className="h-7 w-7" />}
                            title="No entities found"
                            description="Try adjusting your query parameters."
                          />
                        )}
                      </div>
                    </ScrollArea>
                  </TabsContent>

                  <TabsContent value="relationships">
                    <ScrollArea className="h-[400px]">
                      <div className="space-y-2">
                        {graphData.relationships.map((rel) => (
                          <div key={rel.relationshipId} className="flex items-center gap-3 p-3 bg-muted/30 rounded-lg">
                            <div className="p-1.5 bg-blue-100 rounded">
                              <GitGraph className="h-4 w-4 text-blue-600" />
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="font-mono text-sm">{rel.sourceEntityId.slice(0, 20)}...</div>
                              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                <Badge variant="secondary" className="gap-1">
                                  <ChevronRight className="h-3 w-3" />
                                  {RELATIONSHIP_TYPE_LABELS[rel.relationshipType] || rel.relationshipType}
                                </Badge>
                                {rel.provenance && <Badge variant="outline" className="text-xs">{rel.provenance}</Badge>}
                                {rel.confidence !== undefined && <Badge variant="outline" className="text-xs">{Math.round(rel.confidence * 100)}% confidence</Badge>}
                              </div>
                              <ChevronRight className="h-4 w-4" />
                              <div className="font-mono text-sm">{rel.targetEntityId.slice(0, 20)}...</div>
                            </div>
                          ))}
                          {graphData.relationships.length === 0 && (
                            <EmptyState icon={<GitGraph className="h-7 w-7" />} title="No relationships found" description="No relationships match the current filters." />
                          )}
                        </div>
                      </ScrollArea>
                  </TabsContent>

                  <TabsContent value="paths">
                    <ScrollArea className="h-[400px]">
                      <div className="space-y-2">
                        {graphData.paths.map((path, idx) => (
                          <div key={idx} className="flex items-center gap-3 p-3 bg-muted/30 rounded-lg">
                            <Badge variant="secondary">{path.depth} hops</Badge>
                            <div className="flex-1 min-w-0 flex items-center gap-2 text-sm">
                              {path.path.map((p, i) => (
                                <span key={i} className="flex items-center gap-1">
                                  {i > 0 && <ChevronRight className="h-3 w-3 text-muted-foreground" />}
                                  <span className="font-mono">{p.slice(0, 12)}</span>
                                </span>
                              ))}
                            </div>
                            {path.entityType && <Badge variant="outline">{ENTITY_TYPE_LABELS[path.entityType] || path.entityType}</Badge>}
                          </div>
                        ))}
                        {graphData.paths.length === 0 && (
                          <EmptyState icon={<GitGraph className="h-7 w-7" />} title="No paths found" description="No traversal paths match the current filters." />
                        )}
                      </div>
                    </ScrollArea>
                  </TabsContent>
                </Tabs>
              </CardContent>
            </Card>
          )}

          {/* Entity Details Panel */}
          {(selectedEntity || entityDetails) && (
            <Card className="border-blue-200">
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle className="flex items-center gap-2">
                  <GitGraph className="h-5 w-5 text-blue-600" />
                  Entity Details
                </CardTitle>
                <Button variant="ghost" size="sm" onClick={() => { setSelectedEntity(null); setEntityDetails(null); }}>
                  <ChevronDown className="h-4 w-4" />
                </Button>
              </CardHeader>
              <CardContent>
                {selectedEntity && (
                  <div className="space-y-4">
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-muted rounded">{getEntityTypeIcon(selectedEntity.entityType)}</div>
                      <div>
                        <h4 className="font-medium">{getEntityDisplayName(selectedEntity)}</h4>
                        <p className="text-sm text-muted-foreground">{selectedEntity.entityId}</p>
                      </div>
                    </div>
                    <Separator />
                    <div className="grid gap-4 md:grid-cols-2">
                      {selectedEntity.feature && <div><label className="text-xs font-medium text-muted-foreground">Feature</label><p>{selectedEntity.feature}</p></div>}
                      {selectedEntity.decision && <div><label className="text-xs font-medium text-muted-foreground">Decision</label><p>{selectedEntity.decision}</p></div>}
                      {selectedEntity.risk && <div><label className="text-xs font-medium text-muted-foreground">Risk</label><p>{selectedEntity.risk}</p></div>}
                      {selectedEntity.sha && <div><label className="text-xs font-medium text-muted-foreground">Commit SHA</label><p className="font-mono">{selectedEntity.sha}</p></div>}
                      {selectedEntity.number && <div><label className="text-xs font-medium text-muted-foreground">PR Number</label><p>#{selectedEntity.number}</p></div>}
                      {selectedEntity.path && <div><label className="text-xs font-medium text-muted-foreground">File Path</label><p className="font-mono truncate">{selectedEntity.path}</p></div>}
                      {selectedEntity.author && <div><label className="text-xs font-medium text-muted-foreground">Author</label><p>{selectedEntity.author}</p></div>}
                      {selectedEntity.createdAt && <div><label className="text-xs font-medium text-muted-foreground">Created</label><p>{new Date(selectedEntity.createdAt).toLocaleString()}</p></div>}
                    </div>
                    {entityDetails && (
                      <>
                        <Separator />
                        <h4 className="font-medium">Related Changes</h4>
                        <div className="grid gap-4 md:grid-cols-4 text-center">
                          <div className="p-3 bg-muted rounded"><p className="text-2xl font-bold">{entityDetails.relatedCommits?.length || 0}</p><p className="text-xs text-muted-foreground">Commits</p></div>
                          <div className="p-3 bg-muted rounded"><p className="text-2xl font-bold">{entityDetails.relatedPRs?.length || 0}</p><p className="text-xs text-muted-foreground">Pull Requests</p></div>
                          <div className="p-3 bg-muted rounded"><p className="text-2xl font-bold">{entityDetails.relatedFiles?.length || 0}</p><p className="text-xs text-muted-foreground">Files</p></div>
                          <div className="p-3 bg-muted rounded"><p className="text-2xl font-bold">{entityDetails.relatedDecisions?.length || 0}</p><p className="text-xs text-muted-foreground">Decisions</p></div>
                        </div>
                        {entityDetails.relationships && entityDetails.relationships.length > 0 && (
                          <>
                            <Separator />
                            <h4 className="font-medium">Relationships ({entityDetails.relationships.length})</h4>
                            <ScrollArea className="h-[200px]">
                              <div className="space-y-2">
                                {entityDetails.relationships.slice(0, 20).map((rel) => (
                                  <div key={rel.relationshipId} className="text-sm text-muted-foreground flex items-center gap-2">
                                    <span className="font-mono">{rel.sourceEntityId?.slice(0, 12)}</span>
                                    <ChevronRight className="h-3 w-3" />
                                    <Badge variant="secondary">{RELATIONSHIP_TYPE_LABELS[rel.relationshipType] || rel.relationshipType}</Badge>
                                    <ChevronRight className="h-3 w-3" />
                                    <span className="font-mono">{rel.targetEntityId?.slice(0, 12)}</span>
                                  </div>
                                ))}
                              </div>
                            </ScrollArea>
                          </>
                        )}
                      </>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Summary Tab */}
        <TabsContent value="summary" className="space-y-4">
          {summaryData ? (
            <>
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><GitGraph className="h-5 w-5" /> Repository Graph Summary</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid gap-4 md:grid-cols-4">
                    <div className="p-4 bg-muted rounded-lg"><p className="text-3xl font-bold">{summaryData.summary.totalEntities}</p><p className="text-sm text-muted-foreground">Total Entities</p></div>
                    <div className="p-4 bg-muted rounded-lg"><p className="text-3xl font-bold">{summaryData.summary.totalRelationships}</p><p className="text-sm text-muted-foreground">Total Relationships</p></div>
                    <div className="p-4 bg-muted rounded-lg"><p className="text-3xl font-bold">{summaryData.summary.graphDensity.toFixed(2)}</p><p className="text-sm text-muted-foreground">Graph Density</p></div>
                    <div className="p-4 bg-muted rounded-lg"><p className="text-3xl font-bold">{Object.keys(summaryData.entityCounts).length}</p><p className="text-sm text-muted-foreground">Entity Types</p></div>
                  </div>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <h4 className="font-medium mb-2">Entity Counts</h4>
                      <div className="space-y-2">
                        {Object.entries(summaryData.entityCounts).map(([type, count]) => (
                          <div key={type} className="flex items-center justify-between text-sm">
                            <span className="flex items-center gap-2">{getEntityTypeIcon(type)} {ENTITY_TYPE_LABELS[type] || type}</span>
                            <Badge variant="secondary">{count}</Badge>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div>
                      <h4 className="font-medium mb-2">Relationship Counts</h4>
                      <div className="space-y-2">
                        {Object.entries(summaryData.relationshipCounts).filter(([, count]) => count > 0).map(([type, count]) => (
                          <div key={type} className="flex items-center justify-between text-sm">
                            <span>{RELATIONSHIP_TYPE_LABELS[type] || type}</span>
                            <Badge variant="secondary">{count}</Badge>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                  {summaryData.recentActivity.length > 0 && (
                    <div>
                      <h4 className="font-medium mb-2">Recent Activity</h4>
                      <ScrollArea className="h-[300px]">
                        <div className="space-y-2">
                          {summaryData.recentActivity.slice(0, 10).map((entity) => (
                            <Button key={entity.entityId} variant="outline" className="w-full justify-start text-left gap-3 p-3 hover:bg-muted/50" onClick={() => handleEntityClick(entity)}>
                              <div className="flex items-center gap-3 flex-1 min-w-0">
                                <div className="p-1.5 bg-muted rounded">{getEntityTypeIcon(entity.entityType)}</div>
                                <div className="flex-1 min-w-0">
                                  <div className="font-medium truncate">{getEntityDisplayName(entity)}</div>
                                  <div className="flex items-center gap-2 text-xs text-muted-foreground">{getEntityTypeBadge(entity.entityType)} {entity.commitHash && <span className="font-mono">{entity.commitHash.slice(0, 12)}</span>}</div>
                                </div>
                                <ChevronRight className="h-4 w-4 text-muted-foreground" />
                              </div>
                            </Button>
                          ))}
                        </div>
                      </ScrollArea>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </>
          ) : (
            <Card><CardContent className="py-12 text-center"><Loader2 className="h-8 w-8 animate-spin mx-auto text-muted-foreground mb-4" /><p className="text-muted-foreground">Loading repository summary...</p></CardContent></Card>
          )}
        </TabsContent>

        {/* Search Tab */}
        <TabsContent value="search" className="space-y-4">
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><Search className="h-5 w-5" /> Find Related Context</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">Find context connected to an entity via the knowledge graph, optionally combined with semantic search.</p>
              <div className="grid gap-4 md:grid-cols-2">
                <div><label className="text-sm font-medium mb-1 block">Entity Type</label><Select value={selectedEntityType} onValueChange={setSelectedEntityType}><SelectTrigger><SelectValue placeholder="Select entity type" /></SelectTrigger><SelectContent><SelectItem value="commit">Commit</SelectItem><SelectItem value="pull_request">Pull Request</SelectItem><SelectItem value="file">File</SelectItem><SelectItem value="engineering_decision">Engineering Decision</SelectItem></SelectContent></Select></div>
                <div><label className="text-sm font-medium mb-1 block">Entity ID</label><Input placeholder="e.g., commit:repo:...:sha" value={entityId} onChange={(e) => setEntityId(e.target.value)} /></div>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div><label className="text-sm font-medium mb-1 block">Natural Language Query (Optional)</label><Input placeholder="e.g., Why was this implementation chosen?" value="" onChange={() => {}} /></div>
                <div><label className="text-sm font-medium mb-1 block">Max Depth</label><Select value={maxDepth.toString()} onValueChange={(e) => setMaxDepth(parseInt(e))}><SelectTrigger className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1">1</SelectItem><SelectItem value="2">2</SelectItem><SelectItem value="3">3</SelectItem></SelectContent></Select></div>
              </div>
              <Button disabled={!entityId} className="w-full"><Search className="mr-2 h-4 w-4" /> Find Related Context</Button>
              <div className="text-xs text-muted-foreground"><Info className="h-3 w-3 inline mr-1" /> This will traverse the knowledge graph from the selected entity and optionally combine results with semantic search.</div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export function GraphExplorer({ projectId, token }: { projectId?: string; token?: string }) {
  if (!projectId || !token) {
    return (
      <EmptyState
        icon={<GitGraph className="h-7 w-7" />}
        title="Graph Explorer"
        description="Configure your project ID and API token in Settings to explore the engineering knowledge graph."
      />
    );
  }

  return <GraphExplorerContent projectId={projectId} token={token} />;
}