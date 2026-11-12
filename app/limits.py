"""失败限速：同一来源 15 分钟内失败 10 次后暂时拒绝。"""
import time
from collections import defaultdict, deque

WINDOW, MAX_FAILS = 900, 10
# shortcut: 计数在进程内存里，重启清零；Dockerfile 只起一个进程所以够用，多进程/多机部署时改存 Redis 或数据库
_fails = defaultdict(deque)


def blocked(key):
    q = _fails[key]
    while q and q[0] < time.time() - WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILS


def fail(key):
    _fails[key].append(time.time())


def client_ip(request):
    return request.client.host if request and request.client else "?"
