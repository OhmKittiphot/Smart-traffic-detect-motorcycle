# Runs a model's forward pass as a CUDA graph. On this GPU the models wait on the CPU launching many
# small kernels one by one; a graph replays them in one call (one YOLO frame: 18 ms -> 4.8 ms).
# A graph fits one input shape, so only common shapes get one and crop batches are padded to a few sizes.
import torch

from .utils import log

# Sizes a batch is padded up to (copies of one of its items, whose results are dropped)
BATCH_SIZES = (1, 2, 4, 8)


def split_batch(items):
    # items (a list or a tensor) in parts of at most BATCH_SIZES[-1], each with the size from BATCH_SIZES
    # it is to be padded up to: 11 items -> (8 items, 8), (3 items, 4).
    for start in range(0, len(items), BATCH_SIZES[-1]):
        part = items[start:start + BATCH_SIZES[-1]]
        yield part, next(size for size in BATCH_SIZES if size >= len(part))


def _clone(value):
    # A copy of a model output (tensors, possibly nested in tuples, lists and dicts).
    if torch.is_tensor(value):
        return value.clone()
    if isinstance(value, (list, tuple)):
        return type(value)(_clone(v) for v in value)
    if isinstance(value, dict):
        return {k: _clone(v) for k, v in value.items()}
    return value


class GraphedForward:
    # Calls forward(x) through one CUDA graph per shape and dtype of x and returns a copy of the output.
    # after(output) runs the part a graph cannot hold; keep() returns outside tensors a graph reads (kept alive with it).

    def __init__(self, forward, after=None, keep=None, record_after: int = 2, max_graphs: int = 8):
        self.forward = forward
        self.after = after
        self.keep = keep
        self.record_after = record_after
        self.max_graphs = max_graphs
        self.graphs = {}   # (shape, dtype) -> (graph, its input tensor, its output, keep()); None: no graph
        self.calls = {}    # (shape, dtype) -> calls so far, until a graph is recorded for it
        self.pool = None

    def _record(self, x: torch.Tensor):
        static_in = x.clone()
        # Run a few times on a side stream first, as recording requires (cuDNN picks its kernels then)
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):
                self.forward(static_in)
        torch.cuda.current_stream().wait_stream(stream)
        graph = torch.cuda.CUDAGraph()
        # thread_local: other threads may use the GPU meanwhile (only this thread's work is recorded)
        with torch.cuda.graph(graph, pool=self.pool, capture_error_mode="thread_local"):
            static_out = self.forward(static_in)
        self.pool = graph.pool()
        return graph, static_in, static_out, self.keep() if self.keep else None

    def _graph_for(self, x: torch.Tensor):
        key = (tuple(x.shape), x.dtype)
        if key in self.graphs:
            return self.graphs[key]
        self.calls[key] = self.calls.get(key, 0) + 1
        if self.calls[key] < self.record_after or len(self.graphs) >= self.max_graphs:
            return None
        try:
            self.graphs[key] = self._record(x)
        except RuntimeError as e:
            log.warning("[CUDA graph] Not recorded for input %s (%s); running it without one.", key[0], e)
            self.graphs[key] = None
        del self.calls[key]
        return self.graphs[key]

    def __call__(self, x: torch.Tensor, *args, **kwargs):
        graph = None if args or any(kwargs.values()) else self._graph_for(x)
        if graph is None:   # an option the graph was not recorded with (ultralytics' augment, visualize...)
            output = self.forward(x, *args, **kwargs)
        else:
            graph, static_in, static_out, _ = graph
            static_in.copy_(x)
            graph.replay()
            output = _clone(static_out)
        return self.after(output) if self.after else output


def graph_yolo(yolo) -> None:
    # Run an ultralytics YOLO model, loaded on a CUDA device, through CUDA graphs (see GraphedForward).
    net = yolo.model   # the DetectionModel that ultralytics' predict() calls
    head = net.model[-1]
    # The head keeps its anchor grid in an attribute and replaces it whenever the input shape (batch size
    # included) changes; a graph recorded earlier would then read the freed old grid (NaN boxes, illegal
    # memory access). Each graph keeps the grid it was recorded with (the grid only depends on the image size)
    anchor_grid = lambda: (getattr(head, "anchors", None), getattr(head, "strides", None))
    if not getattr(head, "end2end", False):
        net.forward = GraphedForward(net.forward, keep=anchor_grid)
        return
    # The end-to-end head (YOLO26) picks its best boxes with a CPU tensor, which a graph cannot record,
    # so that step runs after the replay
    pick_boxes = head.postprocess
    head.postprocess = lambda preds: preds

    def pick_after(output):
        boxes, preds = output
        return pick_boxes(boxes), preds
    net.forward = GraphedForward(net.forward, after=pick_after, keep=anchor_grid)
