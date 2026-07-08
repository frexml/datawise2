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

            const [resultRes, lineageRes, stageLineageRes] =
                await Promise.allSettled([
                    axios.get(`/api/results/${jobId}`),
                    axios.get(`/api/jobs/${jobId}/lineage`),
                    axios.get(`/api/jobs/${jobId}/stage-lineage`),
                ]);
            if (cancelled) return;

            if (resultRes.status === 'fulfilled') setResult(resultRes.value.data);
            else console.error('Failed to load result:', resultRes.reason);

            if (lineageRes.status === 'fulfilled') setLineageData(lineageRes.value.data);
            else console.error('Failed to load lineage:', lineageRes.reason);

            if (stageLineageRes.status === 'fulfilled') setStageLineageData(stageLineageRes.value.data);
            else console.error('Failed to load stage lineage:', stageLineageRes.reason);

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

    // Collapsible sections state
    const [isOverviewExpanded, setIsOverviewExpanded] = useState(true);
    const [isSummaryExpanded, setIsSummaryExpanded] = useState(true);
    const [isLineageExpanded, setIsLineageExpanded] = useState(true);
    const [lineageData, setLineageData] = useState([]);
    const [loadingLineage, setLoadingLineage] = useState(false);
    const [lineageSearchTerm, setLineageSearchTerm] = useState('');
    const [isStageLineageExpanded, setIsStageLineageExpanded] = useState(true);
    const [stageLineageData, setStageLineageData] = useState([]);
    const [loadingStageLineage, setLoadingStageLineage] = useState(false);
    const [stageLineageSearchTerm, setStageLineageSearchTerm] = useState('');
    const [isGraphExpanded, setIsGraphExpanded] = useState(true);
    const [isFullscreen, setIsFullscreen] = useState(false);

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

    if (loading) return <div className="text-center mt-10">Loading...</div>;
    if (error) return <div className="text-center mt-10 text-red-600">{error}</div>;
    if (!job) return <div className="text-center mt-10">Job not found.</div>;

    // Calculate stats
    const stageCount = nodes.filter(n => !n.id.includes('P')).length;
    const linkCount = edges.filter(e => e.data && e.data.fullData).length;

    return (
        <div className="min-h-screen flex flex-col">
            <div className="bg-white shadow px-4 py-4 flex justify-between items-center z-10">
                <div className="flex items-center">
                    <Link to="/" className="text-indigo-600 hover:text-indigo-900 mr-4">← Back</Link>
                    <h3 className="text-lg font-medium text-gray-900">{job.filename}</h3>
                </div>
                <span className={`px-2 py-1 text-xs font-semibold rounded-full ${job.status === 'COMPLETED' ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'
                    }`}>
                    {job.status}
                </span>
            </div>

            {/* Job Overview */}
            <div className="bg-gray-50 border-b shadow-sm">
                <div
                    className="px-6 py-3 flex justify-between items-center cursor-pointer hover:bg-gray-100 transition-colors"
                    onClick={() => setIsOverviewExpanded(!isOverviewExpanded)}
                >
                    <h2 className="text-lg font-bold text-gray-800 flex items-center">
                        <span className="mr-2">📋</span> Job Overview
                    </h2>
                    <span className="text-gray-500 text-xl">
                        {isOverviewExpanded ? '▲' : '▼'}
                    </span>
                </div>

                {isOverviewExpanded && (
                    <div className="px-6 py-4 border-t bg-white">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <div className="p-3 bg-gray-50 rounded border">
                                <div className="text-xs text-gray-500 uppercase font-semibold">Job Identifier</div>
                                <div className="text-sm font-medium text-gray-900 truncate" title={job.raw_json?._metadata?.job_identifier || job.filename}>
                                    {job.raw_json?._metadata?.job_identifier || job.filename}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 rounded border">
                                <div className="text-xs text-gray-500 uppercase font-semibold">Status</div>
                                <div className={`text-sm font-medium ${job.status === 'COMPLETED' ? 'text-green-600' : 'text-yellow-600'}`}>
                                    {job.status}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 rounded border">
                                <div className="text-xs text-gray-500 uppercase font-semibold">Modified On</div>
                                <div className="text-sm font-medium text-gray-900">
                                    {job.raw_json?._metadata?.date_modified ?
                                        `${job.raw_json._metadata.date_modified} ${job.raw_json._metadata.time_modified || ''}` :
                                        '-'}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 rounded border">
                                <div className="text-xs text-gray-500 uppercase font-semibold">Exported On</div>
                                <div className="text-sm font-medium text-gray-900">
                                    {job.raw_json?._metadata?.export_date ?
                                        `${job.raw_json._metadata.export_date} ${job.raw_json._metadata.export_time || ''}` :
                                        '-'}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 rounded border">
                                <div className="text-xs text-gray-500 uppercase font-semibold">Server</div>
                                <div className="text-sm font-medium text-gray-900 truncate" title={job.raw_json?._metadata?.server_name}>
                                    {job.raw_json?._metadata?.server_name || '-'}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 rounded border">
                                <div className="text-xs text-gray-500 uppercase font-semibold">Tool Version</div>
                                <div className="text-sm font-medium text-gray-900">
                                    {job.raw_json?._metadata?.tool_version || '-'}
                                </div>
                            </div>
                            <div className="p-3 bg-gray-50 rounded border">
                                <div className="text-xs text-gray-500 uppercase font-semibold">Components</div>
                                <div className="text-sm font-medium text-gray-900">
                                    {stageCount} Stages, {linkCount} Links
                                </div>
                            </div>
                        </div>
                        {!job.raw_json?._metadata?.job_identifier && (
                            <div className="mt-2 text-xs text-gray-500 italic">
                                * Detailed metadata not available. Please re-upload the file to extract header information.
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Executive Summary */}
            {result && (
                <div className="bg-gray-50 border-b shadow-sm">
                    <div
                        className="px-6 py-3 flex justify-between items-center cursor-pointer hover:bg-gray-100 transition-colors"
                        onClick={() => setIsSummaryExpanded(!isSummaryExpanded)}
                    >
                        <h2 className="text-lg font-bold text-gray-800 flex items-center">
                            <span className="mr-2">📊</span> Executive Summary
                        </h2>
                        <span className="text-gray-500 text-xl">
                            {isSummaryExpanded ? '▲' : '▼'}
                        </span>
                    </div>

                    {isSummaryExpanded && (
                        <div className="px-6 py-4 border-t">
                            <div className="prose prose-sm max-w-none text-gray-700 bg-white p-4 rounded border">
                                {result.llm_explanation ? (
                                    <Markdown>{result.llm_explanation}</Markdown>
                                ) : (
                                    <p className="italic text-gray-500">
                                        Summary not available for this job. Please re-upload the file to generate a new analysis.
                                    </p>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Graph Section Header */}
            <div className="bg-gray-100 border-b px-6 py-2 flex justify-between items-center shadow-sm">
                <div
                    className="flex items-center cursor-pointer"
                    onClick={() => setIsGraphExpanded(!isGraphExpanded)}
                >
                    <h2 className="text-md font-bold text-gray-700 mr-2">🕸️ Lineage Graph</h2>
                    <span className="text-gray-500 text-sm">
                        {isGraphExpanded ? '▲' : '▼'}
                    </span>
                </div>
                {isGraphExpanded && (
                    <div className="flex gap-2">
                        <button
                            onClick={downloadAsPdf}
                            disabled={isExportingPdf}
                            className="px-3 py-1 bg-green-600 text-white text-sm rounded hover:bg-green-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed"
                        >
                            {isExportingPdf ? '⏳ Exporting...' : '📥 Download PDF'}
                        </button>
                        <button
                            onClick={() => setIsFullscreen(!isFullscreen)}
                            className="px-3 py-1 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700 transition-colors"
                        >
                            {isFullscreen ? '⊗ Exit Fullscreen' : '⛶ Fullscreen'}
                        </button>
                    </div>
                )}
            </div>

            {isGraphExpanded && (
                <div className={isFullscreen ? "fixed inset-0 z-50 flex overflow-hidden bg-white" : "h-[900px] flex overflow-hidden"}>
                    {/* Graph Area */}
                    <div ref={flowRef} className="flex-1 relative bg-gray-50">
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
                            <div className="absolute bottom-4 left-4 bg-white p-2 border rounded shadow text-xs z-10 max-h-64 overflow-y-auto">
                                <h4 className="font-bold mb-2">Legend</h4>
                                <div className="flex items-center mb-1">
                                    <span
                                        className="w-3 h-3 inline-block mr-2 border border-gray-400 flex-shrink-0 rounded-full"
                                        style={{ backgroundColor: '#FFC0CB' }}
                                    ></span>
                                    <span className="truncate" title="Link">Link</span>
                                </div>
                                {Object.entries(stageTypes).map(([type, color]) => (
                                    <div key={type} className="flex items-center mb-1">
                                        <span
                                            className="w-3 h-3 inline-block mr-2 border border-gray-400 flex-shrink-0"
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
                        <div className="w-1/2 bg-white shadow-xl border-l overflow-y-auto p-6 transition-all duration-300 ease-in-out">
                            <div className="flex justify-between items-start mb-4">
                                <h2 className="text-xl font-bold text-gray-900">{selectedNode.name || selectedNode.pin_id}</h2>
                                <button onClick={() => { setSelectedNode(null); setSelectedNodeExplanation(null); }} className="text-gray-400 hover:text-gray-600">
                                    ✕
                                </button>
                            </div>

                            <div className="mb-4">
                                <span className="px-2 py-1 text-xs font-semibold rounded-full border" style={{ backgroundColor: selectedNode.type ? getNodeColor(selectedNode.type) : '#e0e0e0' }}>
                                    {selectedNode.type || (selectedNode.pin_id ? 'Pin' : 'Link')}
                                </span>
                                {selectedNode.source_stage && selectedNode.target_stage && (
                                    <div className="mt-2 text-sm text-gray-600">
                                        <span className="font-medium">{selectedNode.source_stage}</span>
                                        <span className="mx-2">→</span>
                                        <span className="font-medium">{selectedNode.target_stage}</span>
                                    </div>
                                )}
                                {selectedNode.pin_id && (
                                    <div className="mt-2 text-sm text-gray-600">
                                        <div><strong>Stage:</strong> {selectedNode.stage?.name}</div>
                                        <div><strong>ID:</strong> {selectedNode.pin_id}</div>
                                    </div>
                                )}
                            </div>

                            {loadingExplanation ? (
                                <div className="text-center py-4">
                                    <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                                    <p className="mt-2 text-sm text-gray-500">Loading analysis...</p>
                                </div>
                            ) : selectedNodeExplanation ? (
                                <div className="prose prose-sm max-w-none">
                                    <Markdown>{selectedNodeExplanation}</Markdown>
                                </div>
                            ) : (
                                <p className="text-gray-500 italic">No analysis available.</p>
                            )}

                            <div className="mt-8">
                                <h4 className="text-sm font-bold text-gray-700 mb-2">Properties</h4>
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
            )}

            {/* Stage Lineage */}
            <div className="bg-gray-50 border-b shadow-sm">
                <div className="px-6 py-3 flex justify-between items-center">
                    <div
                        className="flex items-center cursor-pointer hover:opacity-80 transition-opacity"
                        onClick={() => setIsStageLineageExpanded(!isStageLineageExpanded)}
                    >
                        <h2 className="text-lg font-bold text-gray-800 flex items-center">
                            <span className="mr-2">🔀</span> Stage Lineage
                        </h2>
                        <span className="text-gray-500 text-xl ml-2">
                            {isStageLineageExpanded ? '▲' : '▼'}
                        </span>
                    </div>
                    {isStageLineageExpanded && stageLineageData.length > 0 && (
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

                {isStageLineageExpanded && (
                    <div className="px-6 py-4 border-t bg-white">
                        <div className="mb-4">
                            <input
                                type="text"
                                placeholder="Search stage lineage..."
                                value={stageLineageSearchTerm}
                                onChange={(e) => setStageLineageSearchTerm(e.target.value)}
                                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                            />
                        </div>
                        {loadingStageLineage ? (
                            <div className="text-center py-8">
                                <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                                <p className="mt-2 text-sm text-gray-500">Loading stage lineage...</p>
                            </div>
                        ) : stageLineageData.filter(row => 
                            !stageLineageSearchTerm || 
                            Object.values(row).some(val => 
                                String(val).toLowerCase().includes(stageLineageSearchTerm.toLowerCase())
                            )
                        ).length === 0 ? (
                            stageLineageData.length === 0 ? (
                                <p className="text-gray-500 italic">No stage lineage data available.</p>
                            ) : (
                                <p className="text-gray-500 italic">No results found for "{stageLineageSearchTerm}".</p>
                            )
                        ) : (
                            <div className="overflow-x-auto overflow-y-auto max-h-96">
                                <table className="min-w-full divide-y divide-gray-200 text-sm">
                                    <thead className="bg-gray-50 sticky top-0">
                                        <tr>
                                            {stageLineageData.length > 0 && Object.keys(stageLineageData[0]).map(header => (
                                                <th key={header} className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase" style={{minWidth: '150px'}}>
                                                    {header.replace(/_/g, ' ')}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody className="bg-white divide-y divide-gray-200">
                                        {stageLineageData.filter(row => 
                                            !stageLineageSearchTerm || 
                                            Object.values(row).some(val => 
                                                String(val).toLowerCase().includes(stageLineageSearchTerm.toLowerCase())
                                            )
                                        ).map((row, idx) => (
                                            <tr key={idx} className="hover:bg-gray-50">
                                                {Object.values(row).map((val, i) => (
                                                    <td key={i} className="px-3 py-2 text-gray-900 relative group">
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
                )}
            </div>

            {/* End-to-End Lineage */}
            <div className="bg-gray-50 border-b shadow-sm">
                <div className="px-6 py-3 flex justify-between items-center">
                    <div
                        className="flex items-center cursor-pointer hover:opacity-80 transition-opacity"
                        onClick={() => setIsLineageExpanded(!isLineageExpanded)}
                    >
                        <h2 className="text-lg font-bold text-gray-800 flex items-center">
                            <span className="mr-2">🔗</span> End-to-End Lineage
                        </h2>
                        <span className="text-gray-500 text-xl ml-2">
                            {isLineageExpanded ? '▲' : '▼'}
                        </span>
                    </div>
                    {isLineageExpanded && lineageData.length > 0 && (
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

                {isLineageExpanded && (
                    <div className="px-6 py-4 border-t bg-white">
                        <div className="mb-4">
                            <input
                                type="text"
                                placeholder="Search lineage (table, field, path, transformation...)..."
                                value={lineageSearchTerm}
                                onChange={(e) => setLineageSearchTerm(e.target.value)}
                                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                            />
                        </div>
                        {loadingLineage ? (
                            <div className="text-center py-8">
                                <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                                <p className="mt-2 text-sm text-gray-500">Analyzing lineage...</p>
                            </div>
                        ) : lineageData.filter(row => 
                            !lineageSearchTerm || 
                            Object.values(row).some(val => 
                                String(val).toLowerCase().includes(lineageSearchTerm.toLowerCase())
                            )
                        ).length === 0 ? (
                            lineageData.length === 0 ? (
                                <p className="text-gray-500 italic">No lineage data available.</p>
                            ) : (
                                <p className="text-gray-500 italic">No results found for "{lineageSearchTerm}".</p>
                            )
                        ) : (
                            <div className="overflow-x-auto overflow-y-auto max-h-96">
                                <table className="min-w-full divide-y divide-gray-200 text-sm">
                                    <thead className="bg-gray-50 sticky top-0">
                                        <tr>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Target Table</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Target Field</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Source Table</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Source Field</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '120px'}}>Source Link</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '120px'}}>Target Link</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '200px'}}>Full Path</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase" style={{minWidth: '80px'}}>Total Hops</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '200px'}}>Transformation Logic</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '150px'}}>Transformation Explanation</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase resize-x overflow-auto" style={{minWidth: '120px'}}>Transformation Type</th>
                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase" style={{minWidth: '100px'}}>Cardinality</th>
                                        </tr>
                                    </thead>
                                    <tbody className="bg-white divide-y divide-gray-200">
                                        {lineageData.filter(row => 
                                            !lineageSearchTerm || 
                                            Object.values(row).some(val => 
                                                String(val).toLowerCase().includes(lineageSearchTerm.toLowerCase())
                                            )
                                        ).map((row, idx) => (
                                            <tr key={idx} className="hover:bg-gray-50">
                                                <td className="px-3 py-2 text-gray-900 relative group">
                                                    <div className="max-w-xs truncate">{row.target_table}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.target_table}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 relative group">
                                                    <div className="max-w-xs truncate">{row.target_field}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.target_field}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 relative group">
                                                    <div className="max-w-xs truncate">{row.source_table}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.source_table}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 relative group">
                                                    <div className="max-w-xs truncate">{row.source_field}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.source_field}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-600 relative group">
                                                    <div className="max-w-xs truncate">{row.source_link}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.source_link}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-600 relative group">
                                                    <div className="max-w-xs truncate">{row.target_link}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.target_link}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-600 relative group">
                                                    <div className="max-w-xs truncate">{row.full_path}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.full_path}</div>
                                                </td>
                                                <td className="px-3 py-2 text-center text-gray-900">{row.total_hops}</td>
                                                <td className="px-3 py-2 text-gray-600 relative group">
                                                    <div className="max-w-xs truncate">{row.transformation_logic}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.transformation_logic}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-600 relative group">
                                                    <div className="max-w-xs truncate">{row.transformation_explanation}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.transformation_explanation}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 relative group">
                                                    <div className="max-w-xs truncate">{row.transformation_type}</div>
                                                    <div className="hidden group-hover:block absolute z-50 bg-gray-900 text-white text-xs rounded py-2 px-3 shadow-lg whitespace-normal max-w-md left-0 top-full mt-1">{row.transformation_type}</div>
                                                </td>
                                                <td className="px-3 py-2 text-gray-900 relative group">
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
                )}
            </div>
        </div>
    );
};

export default JobDetails;
