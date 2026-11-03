"""Synthetic text-job HTTP contract for browser interaction tests.

No local model is invoked. Auth/CAS/process behavior belongs to the real
backend suites; this fixture measures rendering, user edits and stale UI state.
"""
import copy
import re
from urllib.parse import urlsplit


LEADS = (
    '先把房屋参数与预算一起看清楚。', '从面积和租金开始了解这套房。',
    '找房时，可以先核对每月支出。', '这份房源介绍把已知信息逐项列明。',
    '如果正在整理候选房源，可以看看这些参数。', '先了解基本条件，再安排下一步。',
    '把看房需要核对的数字放在一起。', '这一版按费用和空间分别介绍。',
    '为方便对比，先列出面积与楼层。', '下面是这套房目前确认的资料。',
    '预算清单从月租和押金两项开始。', '正在找房的话，可以先收藏这些信息。',
    '了解房源时，不妨先看具体条件。', '这次从房屋大小切入，再介绍租金。',
    '把价格、面积与楼层放进同一份说明。', '这里汇总了可以核对的房屋数据。',
    '这份介绍先给出费用，再列空间参数。', '对比房源时，数字需要清楚可查。',
    '先把基本信息读完，再决定是否进一步了解。', '这一份房源说明保留明确的价格和面积。',
)


def measured_ai(*, complete=False):
    return {'backend': 'mlx', 'operation': 'text', 'model': 'fixture-individual-model',
            'phase': 'complete' if complete else 'generating', 'active': not complete,
            'tokenBased': True, 'generatedTokens': 49, 'tokensPerSecond': 24.5,
            'generationSeconds': 2, 'elapsedSeconds': 2.5}


def accepted_draft(request, ordinal):
    """Authored UI examples, not evidence of neural generation or diversity."""
    fields = request.get('fields', {})
    facts = []
    if fields.get('area'):
        facts.append(str(fields['area'])+'㎡')
    for key, label in [('price', '月租'), ('deposit', '押金')]:
        if fields.get(key):
            facts.append(label+f"{float(fields[key]):,.0f}"+'₽')
    if fields.get('floor') and fields.get('floors'):
        facts.append(str(fields['floor'])+'/'+str(fields['floors'])+'层')
    return {'status': 'review', 'modelAccepted': True, 'fallback': False,
            'generationMode': 'individual-model-prose', 'textSource': 'model',
            'model': 'fixture-individual-model', 'backend': 'mlx',
            'modelContribution': 'full-publication-prose', 'part': request.get('part', 'all'),
            'title': '莫斯科房源资料｜'+str(fields.get('area', '54'))+'㎡',
            'body': LEADS[(ordinal-1) % len(LEADS)]+'\n'+' · '.join(facts),
            'tags': '#莫斯科租房 #房源信息', 'inventoryCount': 0,
            'warnings': ['Тестовая фикстура интерфейса; модель не запускалась.'],
            'ai': measured_ai(complete=True), 'elapsedSeconds': 2.5}


class MockTextJobs:
    def __init__(self, page, namespace, result_factory=accepted_draft, *, result_type='draft',
                 running_reads=1, hold=False):
        self.page = page
        self.namespace = namespace
        self.result_factory = result_factory
        self.result_type = result_type
        self.running_reads = running_reads
        self.hold = hold
        self.requests = []
        self.cancellations = []
        self.jobs = {}
        self.pattern = re.compile(r'/api/'+re.escape(namespace)+r'/text-jobs(?:/[a-f0-9]{32}(?:/cancel)?)?$')
        page.route(self.pattern, self._route)

    def release_all(self):
        self.hold = False
        for job in self.jobs.values():
            job['held'] = False

    def _route(self, route):
        path = urlsplit(route.request.url).path
        prefix = '/api/'+self.namespace+'/text-jobs'
        if path == prefix:
            assert route.request.method == 'POST'
            request = copy.deepcopy(route.request.post_data_json)
            ordinal = len(self.requests)+1
            identifier = f'{ordinal:032x}'
            self.requests.append(request)
            self.jobs[identifier] = {'request': request, 'ordinal': ordinal, 'reads': 0,
                                     'held': self.hold, 'cancelled': False, 'completed': False}
            route.fulfill(status=202, json={'job_id': identifier, 'state': 'queued'})
            return
        identifier = path[len(prefix)+1:].split('/')[0]
        job = self.jobs[identifier]
        if path.endswith('/cancel'):
            assert route.request.method == 'POST'
            self.cancellations.append(identifier)
            if not job['completed']:
                job['cancelled'] = True
            route.fulfill(json={'id': identifier, 'state': 'done' if job['completed'] else 'cancelled'})
            return
        assert route.request.method == 'GET'
        job['reads'] += 1
        if job['cancelled']:
            route.fulfill(json={'id': identifier, 'state': 'cancelled', 'message': 'Генерация отменена. Текущий текст сохранён.'})
        elif job['held'] or job['reads'] <= self.running_reads:
            route.fulfill(json={'id': identifier, 'state': 'running',
                                'message': 'Тестовое задание: модель создаёт описание.',
                                'progress': {'ai': measured_ai()}})
        else:
            if not job['completed']:
                job['result'] = self.result_factory(job['request'], job['ordinal'])
                job['completed'] = True
            route.fulfill(json={'id': identifier, 'state': 'done',
                                'progress': {'ai': measured_ai(complete=True)},
                                'resultType': self.result_type, 'result': job['result']})
