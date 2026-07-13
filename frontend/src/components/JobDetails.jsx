import React, { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { useParams, Link } from 'react-router-dom';
import ReactJson from 'react-json-view';
import ReactFlow, {
    Background,
    Controls,
    MiniMap,
    useNodesState,
    useEdgesState,
    MarkerType,
    useReactFlow
} from 'reactflow';
import 'reactflow/dist/style.css';
import Markdown from 'react-markdown';
import dagre from 'dagre';
import { toPng } from 'html-to-image';

const getLayoutedElements = (nodes, edges, direction = 'TB') => {
    const dagreGraph = new dagre.graphlib.Graph({ compound: true });
    dagreGraph.setDefaultEdgeLabel(() => ({}));

    // Adaptive spacing based on graph complexity
    const nodeCount = nodes.length;
    const edgeCount = edges.length;
    const avgConnections = nodeCount > 0 ? edgeCount / nodeCount : 1;
    
    // Dynamic spacing: more space for complex graphs
    const baseNodeSep = avgConnections > 3 ? 120 : 100;
    const baseRankSep = avgConnections > 3 ? 180 : 150;
    
    dagreGraph.setGraph({
        rankdir: direction,
        nodesep: baseNodeSep,
        ranksep: baseRankSep,
        edgesep: 50,
        marginx: 80,
        marginy: 80,
        ranker: 'network-simplex', // Better for complex graphs
        acyclicer: 'greedy'
    });

    nodes.forEach((node) => {
        const width = node.width || 200;
        const height = node.height || 100;
        dagreGraph.setNode(node.id, { width, height });
    });

    // Deduplicate edges and assign weights
    const edgeMap = new Map();
    edges.forEach((edge) => {
        const key = `${edge.source}-${edge.target}`;
        if (!edgeMap.has(key)) {
            edgeMap.set(key, edge);
        }
    });

    edgeMap.forEach((edge) => {
        dagreGraph.setEdge(edge.source, edge.target, {
            minlen: 1,
            weight: 1
        });
    });

    dagre.layout(dagreGraph);

    nodes.forEach((node) => {
        const nodeWithPosition = dagreGraph.node(node.id);
        const width = node.width || 200;
        const height = node.height || 100;

        node.position = {
            x: nodeWithPosition.x - width / 2,
            y: nodeWithPosition.y - height / 2,
        };
    });

    return { nodes, edges };
};

const JobDetailsSkeleton = () => (
    <div className="min-h-screen flex flex-col animate-pulse">
        <div className="bg-white dark:bg-gray-800 shadow px-4 py-4 flex justify-between items-center">
            <div className="h-5 w-64 bg-gray-200 rounded" />
            <div className="h-5 w-20 bg-gray-200 rounded-full" />
        </div>
        <div className="p-6 space-y-4">
            <div className="h-24 bg-gray-100 dark:bg-gray-700 rounded border dark:border-gray-700" />
            <div className="h-40 bg-gray-100 dark:bg-gray-700 rounded border dark:border-gray-700" />
            <div className="h-96 bg-gray-100 dark:bg-gray-700 rounded border dark:border-gray-700" />
        </div>
    </div>
);

const SummaryCard = ({ title, text, emptyText }) => {
    const [copied, setCopied] = useState(false);

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(text || '');
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch (err) {
            console.error('Failed to copy:', err);
        }
    };

    return (
        <div>
            <div className="flex items-center justify-between mb-1">
                <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">{title}</div>
                {text && (
                    <button
                        onClick={copy}
                        className="text-xs text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 flex items-center gap-1"
                    >
                        {copied ? '✓ Copied' : '📋 Copy'}
                    </button>
                )}
            </div>
            <div className="prose prose-sm max-w-none text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 p-4 rounded border dark:border-gray-700 h-full">
                {text ? <Markdown>{text}</Markdown> : <p className="italic text-gray-500 dark:text-gray-400">{emptyText}</p>}
            </div>
        </div>
    );
};

const JobDetails = () => {
    const { jobId } = useParams();
    const [job, setJob] = useState(null);
    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    // Graph State
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [selectedNode, setSelectedNode] = useState(null);
    const [selectedNodeExplanation, setSelectedNodeExplanation] = useState(null);
    const [loadingExplanation, setLoadingExplanation] = useState(false);
    const [stageTypes, setStageTypes] = useState({});

    // Cache of LLM explanations keyed by "stage:<id>" or "link:<id>".
    // Lives for the lifetime of this page — clicking the same node twice
    // skips the round-trip entirely.
    const explanationCacheRef = useRef(new Map());

    useEffect(() => {
        let cancelled = false;
        let timerId = null;
        let completedLoaded = false;

        // Parallel-fetch the heavyweight follow-ups once the job is COMPLETED.
        const loadCompletedData = async () => {
            if (completedLoaded || cancelled) return;
            completedLoaded = true;
            setLoadingLineage(true);
            setLoadingStageLineage(true);

            const [resultRes, lineageRes, stageLineageRes, reviewsRes, inefficienciesRes, scopeiqRes] =
                await Promise.allSettled([
                    axios.get(`/api/results/${jobId}`),
                    axios.get(`/api/jobs/${jobId}/lineage`),
                    axios.get(`/api/jobs/${jobId}/stage-lineage`),
                    axios.get(`/api/jobs/${jobId}/reviews`),
                    axios.get(`/api/jobs/${jobId}/inefficiencies`),
                    axios.get(`/api/jobs/${jobId}/scopeiq`),
                ]);
            if (cancelled) return;

            if (resultRes.status === 'fulfilled') setResult(resultRes.value.data);
            else console.error('Failed to load result:', resultRes.reason);

            if (lineageRes.status === 'fulfilled') setLineageData(lineageRes.value.data);
            else console.error('Failed to load lineage:', lineageRes.reason);

            if (stageLineageRes.status === 'fulfilled') setStageLineageData(stageLineageRes.value.data);
            else console.error('Failed to load stage lineage:', stageLineageRes.reason);

            if (reviewsRes.status === 'fulfilled') setReviews(reviewsRes.value.data);
            else console.error('Failed to load reviews:', reviewsRes.reason);

            if (inefficienciesRes.status === 'fulfilled') setInefficiencies(inefficienciesRes.value.data);
            else console.error('Failed to load inefficiencies:', inefficienciesRes.reason);

            if (scopeiqRes.status === 'fulfilled') setScopeiq(scopeiqRes.value.data);
            else console.error('Failed to load ScopeIQ estimate:', scopeiqRes.reason);

            setLoadingLineage(false);
            setLoadingStageLineage(false);
        };

        // Poll every 5s until the job leaves PROCESSING/PENDING. Pauses when the
        // tab is hidden (cheap: just don't reschedule).
        const tick = async () => {
            if (cancelled) return;
            try {
                const jobRes = await axios.get(`/api/jobs/${jobId}/full`);
                if (cancelled) return;
                setJob(jobRes.data);
                setLoading(false);

                const status = jobRes.data.status;
                if (status === 'COMPLETED') {
                    initializeGraph(jobRes.data);
                    await loadCompletedData();
                    return;  // terminal — stop polling
                }
                if (status === 'FAILED') {
                    return;  // terminal — stop polling
                }
                // PROCESSING or PENDING — keep polling (unless tab hidden)
                if (!document.hidden) {
                    timerId = setTimeout(tick, 5000);
                }
            } catch (err) {
                if (cancelled) return;
                console.error(err);
                setError('Failed to fetch job details.');
                setLoading(false);
            }
        };

        const onVisibilityChange = () => {
            if (!document.hidden && !completedLoaded && !cancelled) tick();
        };
        document.addEventListener('visibilitychange', onVisibilityChange);

        tick();

        return () => {
            cancelled = true;
            if (timerId) clearTimeout(timerId);
            document.removeEventListener('visibilitychange', onVisibilityChange);
        };
    }, [jobId]);

    const generateScopeiqEstimate = async () => {
        setScopeiqGenerating(true);
        try {
            await axios.post(`/api/jobs/${jobId}/scopeiq/generate`);
        } catch (err) {
            console.error('Failed to start ScopeIQ estimate generation:', err);
            setScopeiqGenerating(false);
            return;
        }

        const poll = async () => {
            try {
                const res = await axios.get(`/api/jobs/${jobId}/scopeiq`);
                setScopeiq(res.data);
                if (res.data.status === 'generating' || res.data.status === 'pending') {
                    setTimeout(poll, 4000);
                } else {
                    setScopeiqGenerating(false);
                }
            } catch (err) {
                console.error('Failed to poll ScopeIQ estimate status:', err);
                setScopeiqGenerating(false);
            }
        };
        poll();
    };

    const startEditingTags = () => {
        setDomainInput(job.domain || '');
        setWaveInput(job.wave || '');
        setPriorityInput(!!job.priority);
        setEditingTags(true);
    };

    const saveTags = async () => {
        setSavingTags(true);
        try {
            const res = await axios.patch(`/api/jobs/${jobId}`, {
                domain: domainInput.trim() || null,
                wave: waveInput.trim() || null,
                priority: priorityInput,
            });
            setJob((prev) => ({ ...prev, domain: res.data.domain, wave: res.data.wave, priority: res.data.priority }));
            setEditingTags(false);
        } catch (err) {
            console.error('Failed to save domain/wave/priority:', err);
        } finally {
            setSavingTags(false);
        }
    };

    // distinct colors for stage types (Darker shades for white text)
    const distinctColors = [
        '#C0392B', // Dark Red
        '#27AE60', // Dark Green
        '#2980B9', // Dark Blue
        '#F39C12', // Dark Orange
        '#8E44AD', // Dark Purple
        '#2C3E50', // Dark Blue Grey
        '#D35400', // Pumpkin
        '#16A085', // Green Sea
        '#7F8C8D', // Grey
        '#C2185B'  // Pink
    ];

    const getNodeColor = (type) => {
        if (!type || type === 'Unknown') return '#555'; // Dark grey default
        let hash = 0;
        for (let i = 0; i < type.length; i++) {
            hash = type.charCodeAt(i) + ((hash << 5) - hash);
        }
        const index = Math.abs(hash) % distinctColors.length;
        return distinctColors[index];
    };

    const initializeGraph = (jobData) => {
        if (!jobData.stages) return;

        // Collect unique stage types
        const uniqueTypes = {};
        jobData.stages.forEach(stage => {
            if (stage.type) {
                uniqueTypes[stage.type] = getNodeColor(stage.type);
            }
        });
        setStageTypes(uniqueTypes);

        // 1. Create Initial Stage Nodes with Dynamic Width
        let stageNodes = jobData.stages.map(stage => {
            // Calculate width based on text length (approx 9px per char)
            const textWidth = stage.name.length * 9;
            const nodeWidth = Math.max(180, textWidth + 40); // Min 180, plus padding

            return {
                id: stage.stage_id,
                type: 'default',
                data: {
                    label: stage.name,
                    fullData: stage
                },
                position: { x: 0, y: 0 }, // Initial position, will be updated by layout
                width: nodeWidth, // Store for layout
                height: 50,      // Store for layout
                style: {
                    background: getNodeColor(stage.type),
                    border: '1px solid #fff', // White border for contrast
                    borderRadius: '8px',
                    padding: '12px',
                    width: nodeWidth,
                    fontSize: '14px',
                    fontWeight: 'bold',
                    textAlign: 'center',
                    color: '#ffffff', // White text
                    boxShadow: '0 4px 6px rgba(0,0,0,0.3)' // Shadow for depth
                }
            };
        });

        // 2. Create Logical Edges for Layout (Stage -> Stage)
        const logicalEdges = [];
        if (jobData.links) {
            jobData.links.forEach(link => {
                let sourceStageId = null;
                let targetStageId = null;
                
                // Try to get source stage
                if (link.source_stage && link.source_stage !== "Unknown") {
                    const sourceStage = jobData.stages.find(s => s.name === link.source_stage);
                    if (sourceStage) sourceStageId = sourceStage.stage_id;
                } else if (link.source_pin) {
                    // Derive from pin
                    sourceStageId = link.source_pin.split('P')[0];
                }
                
                // Try to get target stage
                if (link.target_stage && link.target_stage !== "Unknown") {
                    const targetStage = jobData.stages.find(s => s.name === link.target_stage);
                    if (targetStage) targetStageId = targetStage.stage_id;
                } else if (link.target_pin) {
                    // Derive from pin
                    targetStageId = link.target_pin.split('P')[0];
                }
                
                if (sourceStageId && targetStageId) {
                    logicalEdges.push({ source: sourceStageId, target: targetStageId });
                }
            });
        }

        // 3. Apply Layout - Force top-to-bottom
        const { nodes: layoutedStageNodes } = getLayoutedElements(stageNodes, logicalEdges, 'TB');

        const newNodes = [...layoutedStageNodes];
        const newEdges = [];

        // Map for easy lookup
        const stageNodeMap = {};
        layoutedStageNodes.forEach(node => {
            stageNodeMap[node.id] = node;
        });

        // Create Pin Nodes (small circles positioned above/below their parent stages)
        const pinNodeMap = {};
        if (jobData.links && jobData.links.length > 0) {
            // 1. Group pins by Stage and Type (Input/Output)
            const stagePinGroups = {}; // { stageId: { inputs: [], outputs: [] } }

            // Initialize groups for all stages
            Object.keys(stageNodeMap).forEach(stageId => {
                stagePinGroups[stageId] = { inputs: [], outputs: [] };
            });

            // Collect all unique pins and determine their type/stage
            const pinsUsed = new Set();
            jobData.links.forEach(link => {
                if (link.source_pin) pinsUsed.add(link.source_pin);
                if (link.target_pin) pinsUsed.add(link.target_pin);
            });

            pinsUsed.forEach(pinId => {
                if (!pinId) return;

                // Extract stage ID (assuming format V0SxxPxx)
                const stageId = pinId.split('P')[0];
                if (!stagePinGroups[stageId]) return; // Skip if stage not found

                // Determine type
                let isOutput = false;
                // Check if it's used as a source in any link
                const isSource = jobData.links.some(l => l.source_pin === pinId);
                if (isSource) isOutput = true;

                if (isOutput) {
                    stagePinGroups[stageId].outputs.push(pinId);
                } else {
                    stagePinGroups[stageId].inputs.push(pinId);
                }
            });

            // 2. Create Nodes with calculated positions
            Object.entries(stagePinGroups).forEach(([stageId, groups]) => {
                const parentStage = stageNodeMap[stageId];
                if (!parentStage) return;

                const stagePos = parentStage.position;
                const stageWidth = parentStage.width || 180;
                const stageHeight = parentStage.height || 50;
                const pinGap = 40; // Space between pins

                // Position Inputs (TOP)
                groups.inputs.forEach((pinId, index) => {
                    const totalPins = groups.inputs.length;
                    // Center pins: Start from center - half total width + offset
                    const totalWidth = (totalPins - 1) * pinGap;
                    const startX = (stageWidth / 2) - (totalWidth / 2);
                    const xPos = startX + (index * pinGap);

                    pinNodeMap[pinId] = { stageId, isOutput: false };

                    newNodes.push({
                        id: pinId,
                        type: 'default',
                        data: {
                            label: pinId.split('P')[1] || 'P',
                            fullData: { pin_id: pinId, stage: parentStage.data.fullData }
                        },
                        position: {
                            x: stagePos.x + xPos - 15, // -15 to center the 30px pin
                            y: stagePos.y - 40 // Above stage
                        },
                        style: {
                            background: '#3498DB', // Blue for Input
                            border: '2px solid #fff',
                            borderRadius: '50%',
                            padding: '0',
                            width: 30,
                            height: 30,
                            fontSize: '10px',
                            fontWeight: 'bold',
                            color: '#fff',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
                            zIndex: 10
                        }
                    });
                });

                // Position Outputs (BOTTOM)
                groups.outputs.forEach((pinId, index) => {
                    const totalPins = groups.outputs.length;
                    const totalWidth = (totalPins - 1) * pinGap;
                    const startX = (stageWidth / 2) - (totalWidth / 2);
                    const xPos = startX + (index * pinGap);

                    pinNodeMap[pinId] = { stageId, isOutput: true };

                    newNodes.push({
                        id: pinId,
                        type: 'default',
                        data: {
                            label: pinId.split('P')[1] || 'P',
                            fullData: { pin_id: pinId, stage: parentStage.data.fullData }
                        },
                        position: {
                            x: stagePos.x + xPos - 15,
                            y: stagePos.y + stageHeight + 10 // Below stage
                        },
                        style: {
                            background: '#2ECC71', // Green for Output
                            border: '2px solid #fff',
                            borderRadius: '50%',
                            padding: '0',
                            width: 30,
                            height: 30,
                            fontSize: '10px',
                            fontWeight: 'bold',
                            color: '#fff',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
                            zIndex: 10
                        }
                    });
                });
            });
        }

        // Create Edges (Pin -> Pin with link labels)
        if (jobData.links) {
            jobData.links.forEach(link => {
                // Derive source stage from source_pin if source_stage is Unknown
                let sourceStage = null;
                if (link.source_stage && link.source_stage !== "Unknown") {
                    sourceStage = jobData.stages.find(s => s.name === link.source_stage);
                } else if (link.source_pin) {
                    const sourceStageId = link.source_pin.split('P')[0];
                    sourceStage = jobData.stages.find(s => s.stage_id === sourceStageId);
                }
                
                // Derive target stage from target_pin if target_stage is Unknown
                let targetStage = null;
                if (link.target_stage && link.target_stage !== "Unknown") {
                    targetStage = jobData.stages.find(s => s.name === link.target_stage);
                } else if (link.target_pin) {
                    const targetStageId = link.target_pin.split('P')[0];
                    targetStage = jobData.stages.find(s => s.stage_id === targetStageId);
                }

                if (!sourceStage || !targetStage) return;

                // Only create edge if BOTH pins exist
                if (link.source_pin && link.target_pin && 
                    pinNodeMap[link.source_pin] && pinNodeMap[link.target_pin]) {
                    newEdges.push({
                        id: link.link_id || `link-${link.id}`,
                        source: link.source_pin,
                        target: link.target_pin,
                        label: link.name,
                        type: 'smoothstep',
                        markerEnd: { type: MarkerType.ArrowClosed },
                        animated: true,
                        data: { fullData: link },
                        style: {
                            stroke: '#34495E',
                            strokeWidth: 2,
                            cursor: 'pointer'
                        },
                        labelStyle: {
                            fontSize: '11px',
                            fontWeight: '600',
                            fill: '#333'
                        },
                        labelShowBg: true,
                        labelBgStyle: {
                            fill: '#FFC0CB',
                            fillOpacity: 0.9,
                        },
                        labelBgPadding: [6, 3],
                        labelBgBorderRadius: 8,
                    });
                } else {
                    // Fallback: Stage to Stage (when pins missing)
                    newEdges.push({
                        id: link.link_id || `link-${link.id}`,
                        source: sourceStage.stage_id,
                        target: targetStage.stage_id,
                        label: link.name,
                        type: 'smoothstep',
                        markerEnd: { type: MarkerType.ArrowClosed },
                        animated: true,
                        data: { fullData: link },
                        style: {
                            stroke: '#E74C3C',
                            strokeWidth: 2,
                            strokeDasharray: '5,5',
                            cursor: 'pointer'
                        },
                        labelStyle: {
                            fontSize: '10px',
                            fontWeight: '600',
                            fill: '#E74C3C'
                        },
                        labelShowBg: true,
                        labelBgStyle: {
                            fill: '#FFE5E5',
                            fillOpacity: 0.9,
                        },
                        labelBgPadding: [5, 2],
                        labelBgBorderRadius: 6,
                    });
                }
            });
        }

        setNodes(newNodes);
        setEdges(newEdges);
    };

    const fetchExplanation = useCallback(async (kind, id) => {
        if (id == null) return null;
        const key = `${kind}:${id}`;
        const cache = explanationCacheRef.current;
        if (cache.has(key)) return cache.get(key);

        const res = await axios.get(`/api/${kind}s/${id}/explanation`);
        const explanation = res.data.llm_explanation;
        cache.set(key, explanation);
        return explanation;
    }, []);

    const onNodeClick = useCallback(async (event, node) => {
        setSelectedNode(node.data.fullData);
        setSelectedNodeExplanation(null);

        const data = node.data.fullData;
        const kind = data.stage_id ? 'stage' : (data.link_id ? 'link' : null);
        if (!kind) return;

        // Show spinner only if we have to hit the network.
        const cached = explanationCacheRef.current.get(`${kind}:${data.id}`);
        if (cached !== undefined) {
            setSelectedNodeExplanation(cached);
            return;
        }

        setLoadingExplanation(true);
        try {
            setSelectedNodeExplanation(await fetchExplanation(kind, data.id));
        } catch (err) {
            console.error('Failed to load explanation:', err);
        } finally {
            setLoadingExplanation(false);
        }
    }, [fetchExplanation]);

    const onEdgeClick = useCallback(async (event, edge) => {
        setSelectedNode(edge.data.fullData);
        setSelectedNodeExplanation(null);
        const id = edge.data.fullData.id;
        if (id == null) return;

        const cached = explanationCacheRef.current.get(`link:${id}`);
        if (cached !== undefined) {
            setSelectedNodeExplanation(cached);
            return;
        }

        setLoadingExplanation(true);
        try {
            setSelectedNodeExplanation(await fetchExplanation('link', id));
        } catch (err) {
            console.error('Failed to load explanation:', err);
        } finally {
            setLoadingExplanation(false);
        }
    }, [fetchExplanation]);

    const [hoveredNode, setHoveredNode] = useState(null);
    const [mousePosition, setMousePosition] = useState({ x: 0, y: 0 });
    const [reactFlowInstance, setReactFlowInstance] = useState(null);
    const [isExportingPdf, setIsExportingPdf] = useState(false);
    const flowRef = useRef(null);

    // Job Overview stays a persistent collapsible header; everything else
    // (Summary/Inefficiencies/Graph/Stage Lineage/E2E Lineage) is a tab —
    // activeTab replaces the old per-section expand booleans.
    const [isOverviewExpanded, setIsOverviewExpanded] = useState(true);
    const [activeTab, setActiveTab] = useState('summary');
    const [lineageData, setLineageData] = useState([]);
    const [loadingLineage, setLoadingLineage] = useState(false);
    const [lineageSearchTerm, setLineageSearchTerm] = useState('');
    const [stageLineageData, setStageLineageData] = useState([]);
    const [loadingStageLineage, setLoadingStageLineage] = useState(false);
    const [stageLineageSearchTerm, setStageLineageSearchTerm] = useState('');
    const [isFullscreen, setIsFullscreen] = useState(false);

    // Inefficiency detection findings (Neo4j-backed, best-effort)
    const [inefficiencies, setInefficiencies] = useState([]);
    const [scopeiq, setScopeiq] = useState(null);
    const [scopeiqGenerating, setScopeiqGenerating] = useState(false);
    const [editingTags, setEditingTags] = useState(false);
    const [domainInput, setDomainInput] = useState('');
    const [waveInput, setWaveInput] = useState('');
    const [priorityInput, setPriorityInput] = useState(false);
    const [savingTags, setSavingTags] = useState(false);

    // One-time guided walkthrough hint — dismissed permanently per browser
    const [showWalkthrough, setShowWalkthrough] = useState(
        () => localStorage.getItem('dw_walkthrough_dismissed') !== 'true'
    );
    const dismissWalkthrough = () => {
        localStorage.setItem('dw_walkthrough_dismissed', 'true');
        setShowWalkthrough(false);
    };

    // Human review/approval gate — decoupled from job.status. Read-only here;
    // the Pending Reviews queue (/reviews) is where approve/reject happens.
    const [reviews, setReviews] = useState([]);

    // Fit view when nodes are loaded
    useEffect(() => {
        if (reactFlowInstance && nodes.length > 0) {
            // Small delay to ensure rendering is complete
            setTimeout(() => {
                reactFlowInstance.fitView({ padding: 0.2 });
            }, 100);
        }
    }, [reactFlowInstance, nodes.length]);

    const onPaneClick = useCallback(() => {
        setSelectedNode(null);
        setSelectedNodeExplanation(null);
    }, []);

    const onNodeMouseEnter = useCallback((event, node) => {
        // Only show tooltip for pin nodes
        if (node.id.includes('P')) {
            setHoveredNode(node);
            setMousePosition({ x: event.clientX, y: event.clientY });
        }
    }, []);

    const onNodeMouseMove = useCallback((event) => {
        if (hoveredNode) {
            setMousePosition({ x: event.clientX, y: event.clientY });
        }
    }, [hoveredNode]);

    const onNodeMouseLeave = useCallback(() => {
        setHoveredNode(null);
    }, []);

    const downloadAsPdf = async () => {
        if (!reactFlowInstance || !flowRef.current) {
            alert('Diagram not ready. Please try again.');
            return;
        }
        
        setIsExportingPdf(true);
        try {
            const viewport = reactFlowInstance.getViewport();
            const allNodes = reactFlowInstance.getNodes();
            
            if (allNodes.length === 0) {
                alert('No nodes to export.');
                setIsExportingPdf(false);
                return;
            }
            
            // Calculate bounds of entire diagram
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            
            allNodes.forEach(node => {
                const x = node.position.x;
                const y = node.position.y;
                const width = node.width || 200;
                const height = node.height || 100;
                
                minX = Math.min(minX, x);
                minY = Math.min(minY, y);
                maxX = Math.max(maxX, x + width);
                maxY = Math.max(maxY, y + height);
            });
            
            const diagramWidth = maxX - minX;
            const diagramHeight = maxY - minY;
            const padding = 100;
            
            // Calculate zoom to fit entire diagram in a large canvas
            const containerWidth = flowRef.current.offsetWidth;
            const containerHeight = flowRef.current.offsetHeight;
            
            const zoomX = containerWidth / (diagramWidth + padding * 2);
            const zoomY = containerHeight / (diagramHeight + padding * 2);
            const zoom = Math.min(zoomX, zoomY, 1);
            
            // Center the diagram
            const x = -minX * zoom + padding * zoom + (containerWidth - (diagramWidth + padding * 2) * zoom) / 2;
            const y = -minY * zoom + padding * zoom + (containerHeight - (diagramHeight + padding * 2) * zoom) / 2;
            
            // Capture at native resolution with high pixel ratio
            reactFlowInstance.setViewport({ x: -minX + padding, y: -minY + padding, zoom: 1 });
            await new Promise(resolve => setTimeout(resolve, 1000));
            
            // Use SVG export for vector quality (no blur on zoom)
            const { toSvg } = await import('html-to-image');
            const svgDataUrl = await toSvg(flowRef.current, {
                backgroundColor: '#ffffff',
                width: diagramWidth + padding * 2,
                height: diagramHeight + padding * 2
            });
            
            // Convert SVG to high-res PNG
            const canvas = document.createElement('canvas');
            const scale = 4; // 4x resolution
            canvas.width = (diagramWidth + padding * 2) * scale;
            canvas.height = (diagramHeight + padding * 2) * scale;
            const ctx = canvas.getContext('2d');
            ctx.scale(scale, scale);
            
            const img = new Image();
            img.src = svgDataUrl;
            
            img.onload = async () => {
                ctx.drawImage(img, 0, 0);
                const highResDataUrl = canvas.toDataURL('image/png', 1.0);
                
                // Reset viewport
                reactFlowInstance.setViewport(viewport);
                
                const { jsPDF } = await import('jspdf');
                
                const aspectRatio = canvas.width / canvas.height;
                
                // Calculate PDF size to maintain quality
                const maxDimension = 1189; // A0 width in mm
                let pdfWidth, pdfHeight;
                if (aspectRatio > 1) {
                    pdfWidth = maxDimension;
                    pdfHeight = pdfWidth / aspectRatio;
                } else {
                    pdfHeight = maxDimension;
                    pdfWidth = pdfHeight * aspectRatio;
                }
                
                const pdf = new jsPDF({
                    orientation: aspectRatio > 1 ? 'landscape' : 'portrait',
                    unit: 'mm',
                    format: [pdfWidth, pdfHeight]
                });
                
                pdf.addImage(highResDataUrl, 'PNG', 0, 0, pdfWidth, pdfHeight, '', 'NONE');
                pdf.save(`${job.filename}-diagram.pdf`);
                setIsExportingPdf(false);
            };
            
            img.onerror = () => {
                alert('Failed to process diagram image.');
                setIsExportingPdf(false);
            };
        } catch (err) {
            console.error('Failed to export PDF:', err);
            alert('Failed to export PDF: ' + err.message);
            setIsExportingPdf(false);
        }
    };

    if (loading) return <JobDetailsSkeleton />;
    if (error) return <div className="text-center mt-10 text-red-600">{error}</div>;
    if (!job) return <div className="text-center mt-10">Job not found.</div>;

    // Calculate stats
    const stageCount = nodes.filter(n => !n.id.includes('P')).length;
    const linkCount = edges.filter(e => e.data && e.data.fullData).length;

    const processingSeconds = job.updated_at && job.created_at
        ? Math.max(0, Math.round((new Date(job.updated_at) - new Date(job.created_at)) / 1000))
        : null;
    const approvedCount = reviews.filter((r) => r.status === 'approved').length;

    return (
        <div className="min-h-screen flex flex-col">
            <div className="bg-white dark:bg-gray-800 shadow px-4 py-4 flex justify-between items-center z-10">
                <div className="flex items-center">
                    <Link to="/" className="text-indigo-600 dark:text-indigo-400 hover:text-indigo-900 dark:hover:text-indigo-300 mr-4">← Back</Link>
                    <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100">{job.filename}</h3>
                </div>
                <span className={`px-2 py-1 text-xs font-semibold rounded-full ${job.status === 'COMPLETED' ? 'bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300' : 'bg-yellow-100 dark:bg-yellow-900/40 text-yellow-800 dark:text-yellow-300'
                    }`}>
                    {job.status}
                </span>
            </div>

            {job.status === 'COMPLETED' && showWalkthrough && (
                <div className="bg-indigo-50 dark:bg-indigo-900/30 border-b border-indigo-100 dark:border-indigo-800 px-6 py-3 flex items-start justify-between gap-4">
                    <div className="text-sm text-indigo-900 dark:text-indigo-200">
                        <span className="font-semibold">🎬 New here?</span> Click any node in the graph
                        below to see its technical explanation → read the summaries above → approve them
                        in the Review panel → export the S2T register when you're done.
                    </div>
                    <button
                        onClick={dismissWalkthrough}
                        className="text-indigo-400 hover:text-indigo-600 dark:hover:text-indigo-300 text-sm shrink-0"
                        title="Dismiss"
                    >
                        ✕
                    </button>
                </div>
            )}

            {job.status === 'COMPLETED' && (
                <div className="bg-gradient-to-r from-orange-50 to-amber-50 dark:from-orange-900/20 dark:to-amber-900/20 border-b border-orange-100 dark:border-orange-900 px-6 py-3">
                    <div className="flex flex-wrap gap-x-8 gap-y-1 text-sm text-gray-700 dark:text-gray-300">
                        {processingSeconds !== null && (
                            <span>
                                ⏱️ Analyzed in <strong>{processingSeconds}s</strong>
                                {scopeiq?.status === 'completed' ? (
                                    <span className="text-gray-500 dark:text-gray-400">
                                        {' '}— vs. an estimated <strong>{scopeiq.total_days_adjusted}</strong> days of manual delivery effort (ScopeIQ)
                                    </span>
                                ) : (
                                    <span className="text-gray-500 dark:text-gray-400"> — vs. days of manual reconstruction</span>
                                )}
                            </span>
                        )}
                        <span>
                            🧠 <strong>{stageCount}</strong> stages documented (technical + business)
                        </span>
                        {inefficiencies.length > 0 && (
                            <span>
                                ⚡ <strong>{inefficiencies.length}</strong> inefficiencies flagged
                            </span>
                        )}
                        {reviews.length > 0 && (
                            <span>
                                🛡️ <strong>{approvedCount}/{reviews.length}</strong> summaries reviewed
                            </span>
                        )}
                    </div>
                </div>
            )}

            {/* Job Overview */}
            <div className="bg-gray-50 dark:bg-gray-900/40 border-b dark:border-gray-700 shadow-sm">
                <div
                    className="px-6 py-3 flex justify-between items-center cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    onClick={() => setIsOverviewExpanded(!isOverviewExpanded)}
                >
                    <h2 className="text-lg font-bold text-gray-800 dark:text-gray-100 flex items-center">
                        <span className="mr-2">📋</span> Job Overview
                    </h2>
                    <span className="text-gray-500 dark:text-gray-400 text-xl">
                        {isOverviewExpanded ? '▲' : '▼'}
                    </span>
                </div>

                {isOverviewExpanded && (
                    <div className="px-6 py-4 border-t dark:border-gray-700 bg-white dark:bg-gray-800">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Job Identifier</div>
                                <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate" title={job.raw_json?._metadata?.job_identifier || job.filename}>
                                    {job.raw_json?._metadata?.job_identifier || job.filename}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Status</div>
                                <div className={`text-sm font-medium ${job.status === 'COMPLETED' ? 'text-green-600' : 'text-yellow-600'}`}>
                                    {job.status}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Modified On</div>
                                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                                    {job.raw_json?._metadata?.date_modified ?
                                        `${job.raw_json._metadata.date_modified} ${job.raw_json._metadata.time_modified || ''}` :
                                        '-'}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Exported On</div>
                                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                                    {job.raw_json?._metadata?.export_date ?
                                        `${job.raw_json._metadata.export_date} ${job.raw_json._metadata.export_time || ''}` :
                                        '-'}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Server</div>
                                <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate" title={job.raw_json?._metadata?.server_name}>
                                    {job.raw_json?._metadata?.server_name || '-'}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Tool Version</div>
                                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                                    {job.raw_json?._metadata?.tool_version || '-'}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Components</div>
                                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                                    {stageCount} Stages, {linkCount} Links
                                </div>
                            </div>
                            {reviews.length > 0 && (
                                <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                    <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Review Status</div>
                                    {reviews.map((review) => (
                                        <div key={review.id} className="text-sm font-medium flex items-center gap-2 flex-wrap">
                                            <span
                                                className={
                                                    'inline-block px-2 py-0.5 rounded text-xs font-semibold ' +
                                                    (review.status === 'approved'
                                                        ? 'bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300'
                                                        : review.status === 'rejected'
                                                        ? 'bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300'
                                                        : review.status === 'regenerating'
                                                        ? 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-800 dark:text-indigo-300'
                                                        : 'bg-yellow-100 dark:bg-yellow-900/40 text-yellow-800 dark:text-yellow-300')
                                                }
                                            >
                                                {review.status.replace(/_/g, ' ')}
                                            </span>
                                            {review.status === 'approved' ? (
                                                review.reviewer && (
                                                    <span className="text-xs text-gray-500 dark:text-gray-400">
                                                        by {review.reviewer}{review.edited_by_reviewer ? ' (edited)' : ''}
                                                    </span>
                                                )
                                            ) : (
                                                <Link to="/reviews" className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline">
                                                    {review.status === 'regenerating' ? 'View progress →' : 'Review now →'}
                                                </Link>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                            <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                <div className="flex items-center justify-between">
                                    <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Domain / Wave / Priority</div>
                                    {!editingTags && (
                                        <button onClick={startEditingTags} className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline">✏️ Edit</button>
                                    )}
                                </div>
                                {editingTags ? (
                                    <div className="mt-1 space-y-1.5">
                                        <input
                                            type="text"
                                            placeholder="Domain (e.g. Fees)"
                                            value={domainInput}
                                            onChange={(e) => setDomainInput(e.target.value)}
                                            className="w-full text-sm border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1"
                                        />
                                        <input
                                            type="text"
                                            placeholder="Wave (e.g. Wave 1)"
                                            value={waveInput}
                                            onChange={(e) => setWaveInput(e.target.value)}
                                            className="w-full text-sm border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1"
                                        />
                                        <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                                            <input
                                                type="checkbox"
                                                checked={priorityInput}
                                                onChange={(e) => setPriorityInput(e.target.checked)}
                                                className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500"
                                            />
                                            ⭐ Priority job
                                        </label>
                                        <div className="flex gap-2">
                                            <button
                                                onClick={saveTags}
                                                disabled={savingTags}
                                                className="px-2 py-1 bg-indigo-600 text-white text-xs rounded hover:bg-indigo-700 disabled:opacity-50"
                                            >
                                                {savingTags ? 'Saving...' : 'Save'}
                                            </button>
                                            <button onClick={() => setEditingTags(false)} className="px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:underline">
                                                Cancel
                                            </button>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                                        {job.domain || <span className="text-gray-400 italic">Unassigned</span>}
                                        {' · '}
                                        {job.wave || <span className="text-gray-400 italic">Unassigned</span>}
                                        {job.priority && <span className="ml-1.5" title="Priority job">⭐</span>}
                                    </div>
                                )}
                            </div>
                            <div className="p-3 bg-gray-50 dark:bg-gray-900/40 rounded border dark:border-gray-700">
                                <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-semibold">Governance Catalog</div>
                                {job.catalog_pushed_at ? (
                                    <a
                                        href={job.catalog_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-sm font-medium inline-flex items-center gap-1 text-indigo-600 dark:text-indigo-400 hover:underline"
                                    >
                                        📚 In Catalog →
                                    </a>
                                ) : (
                                    <span className="text-sm text-gray-500 dark:text-gray-400">
                                        Not yet pushed — pushes automatically once the summary is approved
                                    </span>
                                )}
                            </div>
                        </div>
                        {!job.raw_json?._metadata?.job_identifier && (
                            <div className="mt-2 text-xs text-gray-500 dark:text-gray-400 italic">
                                * Detailed metadata not available. Please re-upload the file to extract header information.
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Tab bar */}
            <div className="bg-gray-100 dark:bg-gray-700 border-b dark:border-gray-700 px-6 flex gap-1 overflow-x-auto shadow-sm">
                {[
                    { key: 'summary', label: 'Summary', icon: '📊' },
                    { key: 'inefficiencies', label: 'Inefficiency Findings', icon: '⚡', count: inefficiencies.length },
                    { key: 'graph', label: 'Lineage Graph', icon: '🕸️' },
                    { key: 'stageLineage', label: 'Stage Lineage', icon: '🔀' },
                    { key: 'e2eLineage', label: 'End-to-End Lineage', icon: '🔗' },
                    { key: 'scopeiq', label: 'ScopeIQ Estimate', icon: '📦' },
                ].map((tab) => (
                    <button
                        key={tab.key}
                        onClick={() => setActiveTab(tab.key)}
                        className={
                            'px-4 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors flex items-center gap-1.5 ' +
                            (activeTab === tab.key
                                ? 'border-orange-600 text-orange-700 dark:text-orange-400'
                                : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200')
                        }
                    >
                        <span>{tab.icon}</span> {tab.label}
                        {!!tab.count && (
                            <span className="ml-1 px-1.5 py-0.5 rounded-full bg-gray-200 dark:bg-gray-600 text-xs">{tab.count}</span>
                        )}
                    </button>
                ))}
            </div>

            {/* Summary tab */}
            {activeTab === 'summary' && result && (
                <div className="bg-gray-50 dark:bg-gray-900/40 border-b dark:border-gray-700 shadow-sm px-6 py-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <SummaryCard
                            title="Technical"
                            text={result.llm_explanation}
                            emptyText="Summary not available for this job. Please re-upload the file to generate a new analysis."
                        />
                        <SummaryCard title="Business" text={result.business_summary} emptyText="Business summary not available." />
                    </div>
                </div>
            )}

            {/* Inefficiency Findings tab */}
            {activeTab === 'inefficiencies' && (
                <div className="bg-gray-50 dark:bg-gray-900/40 border-b dark:border-gray-700 shadow-sm px-6 py-4">
                    {inefficiencies.length === 0 ? (
                        <p className="text-gray-500 dark:text-gray-400 italic">No inefficiencies flagged for this job.</p>
                    ) : (
                    <div className="space-y-2">
                        {inefficiencies.map((finding, idx) => (
                            <div
                                key={idx}
                                className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded p-3 flex items-start gap-3"
                            >
                                <span
                                    className={
                                        'inline-block px-2 py-0.5 rounded text-xs font-semibold whitespace-nowrap ' +
                                        (finding.severity === 'critical' || finding.severity === 'high'
                                            ? 'bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300'
                                            : finding.severity === 'medium'
                                            ? 'bg-yellow-100 dark:bg-yellow-900/40 text-yellow-800 dark:text-yellow-300'
                                            : 'bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-300')
                                    }
                                >
                                    {finding.severity}
                                </span>
                                <div>
                                    <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                                        {finding.pattern_type.replace(/_/g, ' ')}
                                    </div>
                                    <div className="text-sm text-gray-700 dark:text-gray-300">{finding.description}</div>
                                </div>
                            </div>
                        ))}
                    </div>
                    )}
                </div>
            )}

            {/* Lineage Graph tab */}
            {activeTab === 'graph' && (
            <>
            <div className="bg-gray-100 dark:bg-gray-700 border-b dark:border-gray-700 px-6 py-2 flex justify-between items-center shadow-sm">
                <div className="flex items-center">
                    <h2 className="text-md font-bold text-gray-700 dark:text-gray-300 mr-2">🕸️ Lineage Graph</h2>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={downloadAsPdf}
                        disabled={isExportingPdf}
                        className="px-3 py-1 bg-green-600 text-white text-sm rounded hover:bg-green-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed"
                    >
                        {isExportingPdf ? '⏳ Exporting...' : '📥 Download PDF'}
                    </button>
                    <a
                        href={`/api/jobs/${jobId}/export/s2t`}
                        className="px-3 py-1 bg-emerald-700 text-white text-sm rounded hover:bg-emerald-800 transition-colors inline-flex items-center"
                    >
                        📊 Export S2T (Excel)
                    </a>
                    <a
                        href={`/api/jobs/${jobId}/export/evidence-pack`}
                        title="Column-level lineage + review audit trail, regulatory-submission-ready"
                        className="px-3 py-1 bg-slate-700 text-white text-sm rounded hover:bg-slate-800 transition-colors inline-flex items-center"
                    >
                        🏛️ Evidence Pack (PDF)
                    </a>
                    <button
                        onClick={() => setIsFullscreen(!isFullscreen)}
                        className="px-3 py-1 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700 transition-colors"
                    >
                        {isFullscreen ? '⊗ Exit Fullscreen' : '⛶ Fullscreen'}
                    </button>
                </div>
            </div>

                <div className={isFullscreen ? "fixed inset-0 z-50 flex overflow-hidden bg-white dark:bg-gray-800" : "h-[900px] flex overflow-hidden"}>
                    {/* Graph Area */}
                    <div
                        ref={flowRef}
                        className={`flex-1 relative bg-gray-50 dark:bg-gray-900/40 transition-opacity duration-700 ${nodes.length > 0 ? 'opacity-100' : 'opacity-0'
                            }`}
                    >
                        {isFullscreen && (
                            <button
                                onClick={() => setIsFullscreen(false)}
                                className="absolute top-4 right-4 z-50 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 shadow-lg transition-colors font-semibold"
                            >
                                ✕ Exit Fullscreen
                            </button>
                        )}
                        <ReactFlow
                            nodes={nodes}
                            edges={edges}
                            onNodesChange={onNodesChange}
                            onEdgesChange={onEdgesChange}
                            onNodeClick={onNodeClick}
                            onEdgeClick={onEdgeClick}
                            onPaneClick={onPaneClick}
                            onNodeMouseEnter={onNodeMouseEnter}
                            onNodeMouseMove={onNodeMouseMove}
                            onNodeMouseLeave={onNodeMouseLeave}
                            onInit={setReactFlowInstance}
                            fitView
                            minZoom={0.1}
                            maxZoom={4}
                        >
                            <Background />
                            <Controls showZoom showFitView showInteractive />
                            <MiniMap zoomable pannable />
                            <div className="absolute bottom-4 left-4 bg-white dark:bg-gray-800 p-2 border dark:border-gray-700 rounded shadow text-xs z-10 max-h-64 overflow-y-auto">
                                <h4 className="font-bold mb-2">Legend</h4>
                                <div className="flex items-center mb-1">
                                    <span
                                        className="w-3 h-3 inline-block mr-2 border dark:border-gray-700 border-gray-400 flex-shrink-0 rounded-full"
                                        style={{ backgroundColor: '#FFC0CB' }}
                                    ></span>
                                    <span className="truncate" title="Link">Link</span>
                                </div>
                                {Object.entries(stageTypes).map(([type, color]) => (
                                    <div key={type} className="flex items-center mb-1">
                                        <span
                                            className="w-3 h-3 inline-block mr-2 border dark:border-gray-700 border-gray-400 flex-shrink-0"
                                            style={{ backgroundColor: color }}
                                        ></span>
                                        <span className="truncate" title={type}>{type}</span>
                                    </div>
                                ))}
                            </div>
                        </ReactFlow>

                        {/* Tooltip */}
                        {hoveredNode && hoveredNode.data.fullData.pin_id && (
                            <div
                                className="absolute z-50 bg-gray-800 text-white text-xs rounded py-1 px-2 shadow-lg pointer-events-none"
                                style={{
                                    top: mousePosition.y - 40, // Offset slightly above cursor
                                    left: mousePosition.x + 10,
                                    transform: 'translate(-50%, -100%)' // Center horizontally above
                                }}
                            >
                                <div className="font-bold mb-1">Pin: {hoveredNode.data.fullData.pin_id}</div>
                                <div>Type: {hoveredNode.style.background === '#2ECC71' ? 'Output' : 'Input'}</div>
                                <div>Stage: {hoveredNode.data.fullData.stage.name}</div>
                            </div>
                        )}
                    </div>

                    {/* Side Panel */}
                    {selectedNode && (
                        <div className="w-1/2 bg-white dark:bg-gray-800 shadow-xl border-l overflow-y-auto p-6 transition-all duration-300 ease-in-out">
                            <div className="flex justify-between items-start mb-4">
                                <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">{selectedNode.name || selectedNode.pin_id}</h2>
                                <button onClick={() => { setSelectedNode(null); setSelectedNodeExplanation(null); }} className="text-gray-400 dark:text-gray-500 hover:text-gray-600">
                                    ✕
                                </button>
                            </div>

                            <div className="mb-4">
                                <span className="px-2 py-1 text-xs font-semibold rounded-full border dark:border-gray-700" style={{ backgroundColor: selectedNode.type ? getNodeColor(selectedNode.type) : '#e0e0e0' }}>
                                    {selectedNode.type || (selectedNode.pin_id ? 'Pin' : 'Link')}
                                </span>
                                {selectedNode.source_stage && selectedNode.target_stage && (
                                    <div className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                                        <span className="font-medium">{selectedNode.source_stage}</span>
                                        <span className="mx-2">→</span>
                                        <span className="font-medium">{selectedNode.target_stage}</span>
                                    </div>
                                )}
                                {selectedNode.pin_id && (
                                    <div className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                                        <div><strong>Stage:</strong> {selectedNode.stage?.name}</div>
                                        <div><strong>ID:</strong> {selectedNode.pin_id}</div>
                                    </div>
                                )}
                            </div>

                            {loadingExplanation ? (
                                <div className="text-center py-4">
                                    <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                                    <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">Loading analysis...</p>
                                </div>
                            ) : selectedNodeExplanation ? (
                                <div className="prose prose-sm max-w-none">
                                    <Markdown>{selectedNodeExplanation}</Markdown>
                                </div>
                            ) : (
                                <p className="text-gray-500 dark:text-gray-400 italic">No analysis available.</p>
                            )}

                            <div className="mt-8">
                                <h4 className="text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">Properties</h4>
                                <ReactJson
                                    src={selectedNode.properties || selectedNode}
                                    collapsed={1}
                                    displayDataTypes={false}
                                    name={false}
                                    style={{ fontSize: '12px' }}
                                />
                            </div>
                        </div>
                    )}
                </div>
            </>
            )}

            {/* Stage Lineage tab */}
            {activeTab === 'stageLineage' && (
            <div className="bg-gray-50 dark:bg-gray-900/40 border-b dark:border-gray-700 shadow-sm">
                <div className="px-6 py-3 flex justify-between items-center">
                    <h2 className="text-lg font-bold text-gray-800 dark:text-gray-100 flex items-center">
                        <span className="mr-2">🔀</span> Stage Lineage
                    </h2>
                    {stageLineageData.length > 0 && (
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                const headers = Object.keys(stageLineageData[0]);
                                const csvContent = [
                                    headers.join(','),
                                    ...stageLineageData.map(row => headers.map(h => `"${row[h] || ''}"`).join(','))
                                ].join('\n');
                                const blob = new Blob([csvContent], { type: 'text/csv' });
                                const url = URL.createObjectURL(blob);
                                const a = document.createElement('a');
                                a.href = url;
                                a.download = `${job.filename}-stage-lineage.csv`;
                                a.click();
                                URL.revokeObjectURL(url);
                            }}
                            className="px-3 py-1 bg-green-600 text-white text-sm rounded hover:bg-green-700 transition-colors"
                        >
                            📥 Download CSV
                        </button>
                    )}
                </div>

                    <div className="px-6 py-4 border-t dark:border-gray-700 bg-white dark:bg-gray-800">
                        <div className="mb-4">
                            <input
                                type="text"
                                placeholder="Search stage lineage..."
                                value={stageLineageSearchTerm}
                                onChange={(e) => setStageLineageSearchTerm(e.target.value)}
                                className="w-full px-4 py-2 border dark:border-gray-700 border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                            />
                        </div>
                        {loadingStageLineage ? (
                            <div className="text-center py-8">
                                <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                                <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">Loading stage lineage...</p>
                            </div>
                        ) : stageLineageData.filter(row => 
                            !stageLineageSearchTerm || 
                            Object.values(row).some(val => 
                                String(val).toLowerCase().includes(stageLineageSearchTerm.toLowerCase())
                            )
                        ).length === 0 ? (
                            stageLineageData.length === 0 ? (
                                <p className="text-gray-500 dark:text-gray-400 italic">No stage lineage data available.</p>
                            ) : (
                                <p className="text-gray-500 dark:text-gray-400 italic">No results found for "{stageLineageSearchTerm}".</p>
                            )
                        ) : (
                            <div className="overflow-x-auto overflow-y-auto max-h-96">
                                <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700 text-sm">
                                    <thead className="bg-gray-50 dark:bg-gray-900/40 sticky top-0">
                                        <tr>
                                            {stageLineageData.length > 0 && Object.keys(stageLineageData[0]).map(header => (
                                                <th key={header} className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase" style={{minWidth: '150px'}}>
                                                    {header.replace(/_/g, ' ')}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                                        {stageLineageData.filter(row => 
                                            !stageLineageSearchTerm || 
                                            Object.values(row).some(val => 
                                                String(val).toLowerCase().includes(stageLineageSearchTerm.toLowerCase())
                                            )
                                        ).map((row, idx) => (
                                            <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                                                {Object.values(row).map((val, i) => (
                                                    <td key={i} className="px-3 py-2 text-gray-900 dark:text-gray-100 relative group">
                                                        <div className="max-w-xs truncate">{val}</div>
                                                        <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{val}</div>
                                                    </td>
                                                ))}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
            </div>
            )}

            {/* End-to-End Lineage tab */}
            {activeTab === 'e2eLineage' && (
            <div className="bg-gray-50 dark:bg-gray-900/40 border-b dark:border-gray-700 shadow-sm">
                <div className="px-6 py-3 flex justify-between items-center">
                    <h2 className="text-lg font-bold text-gray-800 dark:text-gray-100 flex items-center">
                        <span className="mr-2">🔗</span> End-to-End Lineage
                    </h2>
                    {lineageData.length > 0 && (
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                const headers = ['Target Table', 'Target Field', 'Source Table', 'Source Field', 'Source Link', 'Target Link', 'Full Path', 'Total Hops', 'Transformation Logic', 'Transformation Explanation', 'Transformation Type', 'Cardinality'];
                                const csvContent = [
                                    headers.join(','),
                                    ...lineageData.map(row => [
                                        row.target_table,
                                        row.target_field,
                                        row.source_table,
                                        row.source_field,
                                        row.source_link,
                                        row.target_link,
                                        `"${row.full_path}"`,
                                        row.total_hops,
                                        `"${row.transformation_logic}"`,
                                        `"${row.transformation_explanation}"`,
                                        row.transformation_type,
                                        row.cardinality
                                    ].join(','))
                                ].join('\n');
                                const blob = new Blob([csvContent], { type: 'text/csv' });
                                const url = URL.createObjectURL(blob);
                                const a = document.createElement('a');
                                a.href = url;
                                a.download = `${job.filename}-lineage.csv`;
                                a.click();
                                URL.revokeObjectURL(url);
                            }}
                            className="px-3 py-1 bg-green-600 text-white text-sm rounded hover:bg-green-700 transition-colors"
                        >
                            📥 Download CSV
                        </button>
                    )}
                </div>

                    <div className="px-6 py-4 border-t dark:border-gray-700 bg-white dark:bg-gray-800">
                        <div className="mb-4">
                            <input
                                type="text"
                                placeholder="Search lineage (table, field, path, transformation...)..."
                                value={lineageSearchTerm}
                                onChange={(e) => setLineageSearchTerm(e.target.value)}
                                className="w-full px-4 py-2 border dark:border-gray-700 border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                            />
                        </div>
                        {loadingLineage ? (
                            <div className="text-center py-8">
                                <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                                <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">Analyzing lineage...</p>
                            </div>
                        ) : lineageData.filter(row => 
                            !lineageSearchTerm || 
                            Object.values(row).some(val => 
                                String(val).toLowerCase().includes(lineageSearchTerm.toLowerCase())
                            )
                        ).length === 0 ? (
                            lineageData.length === 0 ? (
                                <p className="text-gray-500 dark:text-gray-400 italic">No lineage data available.</p>
                            ) : (
                                <p className="text-gray-500 dark:text-gray-400 italic">No results found for "{lineageSearchTerm}".</p>
                            )
                        ) : (
                            <div className="overflow-x-auto overflow-y-auto max-h-96">
                                <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700 text-sm">
                                    <thead className="bg-gray-50 dark:bg-gray-900/40 sticky top-0">
                                        <tr>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Target Table</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Target Field</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Source Table</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Source Field</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '120px'}}>Source Link</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '120px'}}>Target Link</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '200px'}}>Full Path</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase" style={{minWidth: '80px'}}>Total Hops</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '200px'}}>Transformation Logic</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Transformation Explanation</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase resize-x overflow-auto" style={{minWidth: '120px'}}>Transformation Type</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase" style={{minWidth: '100px'}}>Cardinality</th>
                                        </tr>
                                    </thead>
                                    <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                                        {lineageData.filter(row => 
                                            !lineageSearchTerm || 
                                            Object.values(row).some(val => 
                                                String(val).toLowerCase().includes(lineageSearchTerm.toLowerCase())
                                            )
                                        ).map((row, idx) => (
                                            <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                                                <td className="px-3 py-2 text-gray-900 dark:text-gray-100 relative group">
                                                    <div className="max-w-xs truncate">{row.target_table}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.target_table}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 dark:text-gray-100 relative group">
                                                    <div className="max-w-xs truncate">{row.target_field}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.target_field}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 dark:text-gray-100 relative group">
                                                    <div className="max-w-xs truncate">{row.source_table}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.source_table}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 dark:text-gray-100 relative group">
                                                    <div className="max-w-xs truncate">{row.source_field}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.source_field}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-600 dark:text-gray-300 relative group">
                                                    <div className="max-w-xs truncate">{row.source_link}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.source_link}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-600 dark:text-gray-300 relative group">
                                                    <div className="max-w-xs truncate">{row.target_link}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.target_link}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-600 dark:text-gray-300 relative group">
                                                    <div className="max-w-xs truncate">{row.full_path}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.full_path}</div>
                                                </td>
                                                <td className="px-3 py-2 text-center text-gray-900 dark:text-gray-100">{row.total_hops}</td>
                                                <td className="px-3 py-2 text-gray-600 dark:text-gray-300 relative group">
                                                    <div className="max-w-xs truncate">{row.transformation_logic}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.transformation_logic}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-600 dark:text-gray-300 relative group">
                                                    <div className="max-w-xs truncate">{row.transformation_explanation}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.transformation_explanation}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 dark:text-gray-100 relative group">
                                                    <div className="max-w-xs truncate">{row.transformation_type}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.transformation_type}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 dark:text-gray-100 relative group">
                                                    <div className="max-w-xs truncate">{row.cardinality}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.cardinality}</div>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
            </div>
            )}

            {/* ScopeIQ Estimate tab */}
            {activeTab === 'scopeiq' && (
                <div className="bg-gray-50 dark:bg-gray-900/40 border-b dark:border-gray-700 shadow-sm px-6 py-4">
                    <ScopeiqEstimateTab
                        scopeiq={scopeiq}
                        generating={scopeiqGenerating}
                        onGenerate={generateScopeiqEstimate}
                        jobId={jobId}
                    />
                </div>
            )}
        </div>
    );
};

const ROLE_LABELS = {
    data_architect: 'Data Architect',
    ai_engineer: 'AI Engineer',
    compliance_lead: 'Compliance Lead',
    migration_engineer: 'Migration Engineer',
    engagement_lead: 'Engagement Lead',
};
const ROLE_ORDER = ['data_architect', 'ai_engineer', 'compliance_lead', 'migration_engineer', 'engagement_lead'];
const DIMENSION_LABELS = {
    tech_stack: 'Technology Stack',
    compliance_regulatory: 'Compliance & Regulatory',
    integration_patterns: 'Integration Patterns',
    delivery_risk: 'Delivery Risk',
};
const DIMENSION_ORDER = ['tech_stack', 'compliance_regulatory', 'integration_patterns', 'delivery_risk'];
const TIER_STYLES = {
    low: 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400 border-green-300 dark:border-green-700',
    medium: 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border-amber-300 dark:border-amber-700',
    high: 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-400 border-red-300 dark:border-red-700',
};

const RoleDaysTable = ({ roleDays, totalLabel }) => {
    const total = ROLE_ORDER.reduce((sum, r) => sum + (roleDays?.[r] || 0), 0);
    return (
        <div className="overflow-x-auto">
            <table className="min-w-full text-sm border dark:border-gray-700 rounded overflow-hidden">
                <thead className="bg-gray-800 text-white">
                    <tr>
                        <th className="px-3 py-2 text-left">Role</th>
                        {ROLE_ORDER.map(r => <th key={r} className="px-3 py-2 text-center">{ROLE_LABELS[r]}</th>)}
                        <th className="px-3 py-2 text-center">{totalLabel || 'Total'}</th>
                    </tr>
                </thead>
                <tbody className="bg-white dark:bg-gray-800">
                    <tr>
                        <td className="px-3 py-2 text-gray-500 dark:text-gray-400">Days</td>
                        {ROLE_ORDER.map(r => <td key={r} className="px-3 py-2 text-center text-gray-900 dark:text-gray-100">{(roleDays?.[r] || 0).toFixed(1)}</td>)}
                        <td className="px-3 py-2 text-center font-semibold text-gray-900 dark:text-gray-100">{total.toFixed(1)}</td>
                    </tr>
                </tbody>
            </table>
        </div>
    );
};

const ScopeiqEstimateTab = ({ scopeiq, generating, onGenerate, jobId }) => {
    const status = scopeiq?.status || 'not_started';

    if (status === 'not_started') {
        return (
            <div className="text-center py-12">
                <div className="mx-auto h-14 w-14 rounded-full bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center text-2xl mb-4">📦</div>
                <h4 className="text-base font-semibold text-gray-900 dark:text-gray-100">No ScopeIQ estimate yet</h4>
                <p className="mt-1 text-sm text-gray-500 dark:text-gray-400 max-w-md mx-auto">
                    Generate a delivery-effort estimate for this job: a ScopeIQ agent decomposes it into four
                    research dimensions, each producing a role-level day breakdown, complexity uplift signals,
                    and risk adjustments.
                </p>
                <button
                    onClick={onGenerate}
                    className="mt-4 px-4 py-2 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700 transition-colors"
                >
                    Generate ScopeIQ Estimate
                </button>
            </div>
        );
    }

    if (generating || status === 'generating' || status === 'pending') {
        return (
            <div className="text-center py-12">
                <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                <p className="mt-3 text-sm text-gray-500 dark:text-gray-400">
                    Researching tech stack, compliance, integration patterns, and delivery risk — this can take up to a minute.
                </p>
            </div>
        );
    }

    if (status === 'failed') {
        return (
            <div className="text-center py-12">
                <div className="mx-auto h-14 w-14 rounded-full bg-red-50 dark:bg-red-900/30 flex items-center justify-center text-2xl mb-4">⚠️</div>
                <h4 className="text-base font-semibold text-gray-900 dark:text-gray-100">Estimate generation failed</h4>
                <p className="mt-1 text-sm text-red-600 dark:text-red-400 max-w-md mx-auto">{scopeiq.error || 'Unknown error.'}</p>
                <button
                    onClick={onGenerate}
                    className="mt-4 px-4 py-2 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700 transition-colors"
                >
                    Retry
                </button>
            </div>
        );
    }

    // completed
    const totalUpliftPct = (scopeiq.uplift_adjustments || []).reduce((sum, s) => sum + (s.uplift_pct || 0), 0);
    const totalRiskDays = (scopeiq.risk_adjustments || []).reduce((sum, r) => sum + (r.impact_days || 0), 0);
    const dimensionsByName = Object.fromEntries((scopeiq.dimensions || []).map(d => [d.dimension, d]));

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div className={`inline-block px-3 py-1.5 rounded border text-sm font-semibold ${TIER_STYLES[scopeiq.complexity_tier] || ''}`}>
                    Complexity Tier: {(scopeiq.complexity_tier || 'unknown').toUpperCase()}
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={onGenerate}
                        className="px-3 py-1 text-sm text-gray-600 dark:text-gray-300 hover:underline"
                    >
                        🔄 Regenerate
                    </button>
                    <a
                        href={`/api/jobs/${jobId}/export/scopeiq-estimate`}
                        className="px-3 py-1 bg-slate-700 text-white text-sm rounded hover:bg-slate-800 transition-colors inline-flex items-center"
                    >
                        📄 Export PDF
                    </a>
                </div>
            </div>

            <div>
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">Base Effort</h3>
                <RoleDaysTable roleDays={scopeiq.role_day_totals} totalLabel="Base Total" />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-sm">
                <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded p-3">
                    <div className="text-gray-500 dark:text-gray-400">Base effort</div>
                    <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">{scopeiq.total_days_base?.toFixed(1)}d</div>
                </div>
                <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded p-3">
                    <div className="text-gray-500 dark:text-gray-400">Complexity uplift</div>
                    <div className="text-lg font-semibold text-indigo-600 dark:text-indigo-400">+{totalUpliftPct.toFixed(0)}%</div>
                </div>
                <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded p-3">
                    <div className="text-gray-500 dark:text-gray-400">Risk adjustment</div>
                    <div className="text-lg font-semibold text-amber-600 dark:text-amber-400">+{totalRiskDays.toFixed(1)}d</div>
                </div>
                <div className="bg-white dark:bg-gray-800 border-2 border-indigo-300 dark:border-indigo-700 rounded p-3">
                    <div className="text-gray-500 dark:text-gray-400">Adjusted total</div>
                    <div className="text-lg font-bold text-gray-900 dark:text-gray-100">{scopeiq.total_days_adjusted?.toFixed(1)}d</div>
                </div>
            </div>

            <div className="space-y-4">
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">Research Dimensions</h3>
                {DIMENSION_ORDER.filter(name => dimensionsByName[name]).map((name) => {
                    const dim = dimensionsByName[name];
                    return (
                        <div key={name} className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg p-4">
                            <h4 className="font-semibold text-gray-900 dark:text-gray-100">{DIMENSION_LABELS[name] || name}</h4>
                            <p className="text-xs italic text-gray-500 dark:text-gray-400 mt-1">Scope: {dim.scope_brief}</p>
                            <p className="text-sm text-gray-700 dark:text-gray-300 mt-2">{dim.findings}</p>
                            <div className="mt-3">
                                <RoleDaysTable roleDays={dim.role_days} />
                            </div>
                            {(dim.uplift_signals || []).length > 0 && (
                                <div className="mt-3 space-y-1">
                                    {dim.uplift_signals.map((s, i) => (
                                        <div key={i} className="text-sm bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-700 rounded px-3 py-2">
                                            <b>+{s.uplift_pct?.toFixed(0)}% — {s.signal}</b>: {s.rationale}
                                        </div>
                                    ))}
                                </div>
                            )}
                            {(dim.risks || []).length > 0 && (
                                <div className="mt-3 space-y-1">
                                    {dim.risks.map((r, i) => (
                                        <div key={i} className="text-sm bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-700 rounded px-3 py-2">
                                            <b>Risk ({r.likelihood} likelihood, +{r.impact_days?.toFixed(1)}d)</b>: {r.description}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

export default JobDetails;
