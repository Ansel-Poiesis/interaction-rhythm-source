var QUERY=new URLSearchParams(location.search);
var IS_DESKTOP=QUERY.get('desktop')==='1';
var IS_NATIVEBAR=QUERY.get('nativebar')==='1';
if(IS_DESKTOP){document.body.classList.add('is-desktop')}
if(IS_NATIVEBAR){document.body.classList.add('nativebar')}

var dataRetryTimer=null;
function isoDate(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}
function todayIso(){return isoDate(new Date())}
function shiftDateIso(date,delta){var d=new Date((date||todayIso())+'T00:00:00');d.setDate(d.getDate()+delta);return isoDate(d)}
var selectedDate=todayIso();
var userPinnedDate=false;
var lastKnownToday=todayIso();
function focusToday(){
  selectedDate=todayIso();
  userPinnedDate=false;
  updateDateControls();
  loadData();
}
var agentView='observations';
var agentPurposeId='understand_personal_rhythm';
var agentPurposeCatalog=[];
var agentObservationFeed=null;
var agentAppsCount=0;
var APP_LABELS={};
function t(key){return (I18N[getLang()]&&I18N[getLang()][key])||(AGENT_EXTRA_I18N[getLang()]&&AGENT_EXTRA_I18N[getLang()][key])||AGENT_EXTRA_I18N.zh[key]||I18N.zh[key]||key}
function setText(id,value){var el=document.getElementById(id);if(el)el.textContent=value}
function toggleApiKeyVisibility(){
  var input=document.getElementById('ai-api-key');
  var btn=document.getElementById('key-visibility-btn');
  if(!input)return;
  var show=input.type==='password';
  input.type=show?'text':'password';
  if(btn){btn.textContent=t(show?'keyHide':'keyShow');btn.setAttribute('aria-label',t(show?'keyHide':'keyShow'));}
}

function applyLanguage(){
  document.documentElement.lang=getLang()==='en'?'en':'zh-CN';
  document.querySelector('.brand-copy span').textContent=t('subtitle');
  var replayBtn=document.querySelector('[data-action="insights"]');
  if(replayBtn){replayBtn.setAttribute('title',t('replay'));replayBtn.setAttribute('aria-label',t('replay'));}
  var appTitle=document.querySelector('.app-section .sec-head h2');
  if(appTitle)appTitle.textContent=t('appRank');
  document.querySelector('[onclick="openSettings()"]').setAttribute('title',t('settings'));
  document.querySelector('[onclick="openSettings()"]').setAttribute('aria-label',t('settings'));
  var minBtn=document.querySelector('[onclick="windowControl(\'minimize\')"]');
  if(minBtn){minBtn.setAttribute('title',t('minimize'));minBtn.setAttribute('aria-label',t('minimize'));}
  var closeBtn=document.querySelector('[onclick="windowControl(\'close\')"]');
  if(closeBtn){closeBtn.setAttribute('title',t('tray'));closeBtn.setAttribute('aria-label',t('tray'));}
  var modalClose=document.querySelector('.modal-close');
  if(modalClose){modalClose.setAttribute('title',t('close'));modalClose.setAttribute('aria-label',t('close'));}
  setText('offline-indicator',t('connecting'));
  setText('lg-less',t('legendLess'));
  setText('lg-more',t('legendMore'));
  var refreshText=document.querySelector('.pull-to-refresh span:last-child'); if(refreshText)refreshText.textContent=t('refreshing');
  updateDateControls();
  var todayTitle=document.querySelector('.today-title');
  if(todayTitle)todayTitle.textContent=t('todayTitle');
  document.getElementById('key-stat-label').textContent=t('keyboard');
  document.getElementById('clicks-stat-label').textContent=t('mouseClicks');
  document.getElementById('scroll-stat-label').textContent=t('mouseScroll');
  document.getElementById('time-stat-label').textContent=t('active');
  var _statLabels=document.querySelectorAll('.stat-card .stat-label');
  if(getLang()==='en'){
    document.documentElement.style.setProperty('--stat-label-w','auto');
    document.documentElement.style.setProperty('--stat-val-minw','0px');
  }else{
    var _statLabelW=0;
    for(var _si=0;_si<_statLabels.length;_si++){if(_statLabels[_si].scrollWidth>_statLabelW)_statLabelW=_statLabels[_si].scrollWidth;}
    document.documentElement.style.setProperty('--stat-label-w',(_statLabelW+2)+'px');
    document.documentElement.style.setProperty('--stat-val-minw','80px');
  }
  document.querySelector('#heat-keyboard + span').textContent=t('keyboardShort');
  document.querySelector('#heat-mouse + span').textContent=t('mouseShort');
  document.querySelector('.heat-label-static').textContent=t('heatmap');
  setText('heat-week-btn',t('week'));
  setText('heat-month-btn',t('month'));
  setText('heat-year-btn',t('year'));
  updateHeatPeriodControls();
  var legend=document.querySelectorAll('.heat-legend span'); if(legend[0])legend[0].textContent=t('less'); if(legend[1])legend[1].textContent=t('more');
  syncAgentChrome();
  var empty=document.querySelector('.app-list .empty'); if(empty)empty.textContent=t('waiting');
  var toastBtns=document.querySelectorAll('.update-actions button');
  if(toastBtns[0])toastBtns[0].textContent=t('view');
  if(toastBtns[1])toastBtns[1].textContent=t('later');
}
function isSelectedToday(){return selectedDate>=todayIso()}
function updateDateControls(){
  var dateEl=document.getElementById('today-date');
  var next=document.getElementById('date-next');
  var prev=document.getElementById('date-prev');
  var atToday=isSelectedToday();
  if(dateEl){dateEl.textContent=fmtDateLabel(selectedDate);dateEl.setAttribute('title',t('todayFocus'));dateEl.setAttribute('aria-label',t('todayFocus'));}
  if(prev){prev.setAttribute('title',t('previousDay'));prev.setAttribute('aria-label',t('previousDay'));}
  if(next){next.disabled=atToday;next.setAttribute('title',t('nextDay'));next.setAttribute('aria-label',t('nextDay'));}
}
function changeDate(delta){
  userPinnedDate=true;
  selectedDate=shiftDateIso(selectedDate,delta);
  if(selectedDate>todayIso())selectedDate=todayIso();
  lastAppsLayoutSignature='';
  monthHeatmap=null;
  yearHeatmap=null;
  resetHeatPeriodCursors();
  updateDateControls();
  loadData();
}
function toggleLang(){setLanguage(getLang()==='en'?'zh':'en',{renderSettings:false});}
function savePreferencePatch(patch){
  return fetch('/api/settings',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(patch)
  }).then(function(r){return r.json()}).then(function(d){
    if(d&&d.ok&&d.settings){
      SETTINGS_STATE=Object.assign({},SETTINGS_DEFAULTS,d.settings||{});
    }
  }).catch(function(){});
}
function setLanguage(lang,options){
  options=options||{};
  lang=lang==='en'?'en':'zh';
  localStorage.setItem('kx-lang',lang);
  applyLanguage();
  if(options.renderSettings!==false){
    renderSettings(SETTINGS_STATE);
  }
  savePreferencePatch({language:lang});
  loadData();
}
function setOfflineMessage(show,text){
  var el=document.getElementById('offline-indicator');
  if(!el)return;
  if(text)el.textContent=text;
  el.classList.toggle('show',!!show);
}

var AGENT_PURPOSE_META={
  understand_personal_rhythm:{
    zh:{title:'理解个人数字节律',description:'根据本地聚合记录与个人参考，形成可追溯的节律观察结论。'},
    en:{title:'Understand personal rhythm',description:'Build traceable observations from local aggregates and personal references.'}
  },
  notice_rhythm_change:{
    zh:{title:'识别近期节律变化',description:'识别本地指标相对个人近期典型范围的可观察变化。'},
    en:{title:'Notice rhythm change',description:'Identify observable changes against your recent personal typical range.'}
  }
};

var AGENT_THEORY_LENS={
  'stage-based-personal-informatics':{
    zh:'记录需要先稳定积累，才适合拿来比较。',
    en:'Records need to accumulate consistently before comparison becomes useful.'
  },
  'quantified-self-collection-exploration':{
    zh:'把数据当作回看线索，不把它当成原因解释。',
    en:'Treat the data as a clue for reflection, not an explanation of cause.'
  },
  'flexible-minimalist-self-tracking':{
    zh:'少量稳定指标，比追求更全面的记录更适合长期回看。',
    en:'A few stable measures are more useful for long-term reflection than exhaustive tracking.'
  },
  'reflection-nature-critical-examination':{
    zh:'变化更适合作为回想当时情境的提示，不是原因判断。',
    en:'Use a change to recall its context, not to assign a cause.'
  },
  'people-like-me-cohort-comparison':{
    zh:'这里仅和你自己的近期范围比较，不和别人比较。',
    en:'This compares only with your own recent range, never with other people.'
  },
  'reflection-theory-practice-gaps':{
    zh:'数值接近通常范围，不代表那天的感受或状态也相同。',
    en:'A value within the usual range does not mean the day felt the same.'
  }
};
var AGENT_READING_TITLES={
  'reading-01':{zh:'采集 ≠ 反思',en:'Collecting is not reflecting'},
  'reading-02':{zh:'反思是检视而非顿悟',en:'Reflection is examination, not epiphany'},
  'reading-03':{zh:'轻量指标更利长期反思',en:'Lightweight measures favor long-term reflection'},
  'reading-04':{zh:'整理先于洞察',en:'Preparation precedes insight'},
  'reading-05':{zh:'只与自己比较',en:'Compare only with yourself'},
  'reading-06':{zh:'数值接近不代表感受相同',en:'In range does not mean the same feeling'},
  'reading-07':{zh:'手感的回返',en:'Returns and rhythm'},
  'reading-08':{zh:'注意力是有限资源',en:'Attention is finite'},
  'reading-09':{zh:'切换也有成本',en:'Switching has a cost'},
  'reading-10':{zh:'时段自带节律',en:'Hours carry their own rhythm'},
  'reading-11':{zh:'记录不必完美',en:'Records need not be perfect'},
  'reading-12':{zh:'每周一小看',en:'A weekly glance'},
  'reading-13':{zh:'数据是镜子不是判决',en:'Data mirrors, does not judge'},
  'reading-14':{zh:'基线需要时间',en:'Baselines need time'},
  'reading-15':{zh:'变化先看方向',en:'Look at direction first'},
  'reading-16':{zh:'低谷也是信息',en:'Low points are information'},
  'reading-17':{zh:'主场不等于效率',en:'Main app is not efficiency'},
  'reading-18':{zh:'键盘不等于专注',en:'Typing is not focus'},
  'reading-19':{zh:'浏览也有价值',en:'Browsing has value'},
  'reading-20':{zh:'休息是节律的一部分',en:'Rest is part of rhythm'},
  'reading-21':{zh:'峰值后的小憩',en:'The lull after a peak'},
  'reading-22':{zh:'日与周的尺度',en:'Day vs week scale'},
  'reading-23':{zh:'连续记录建立信任',en:'Continuity builds trust'},
  'reading-24':{zh:'长尾应用',en:'The long tail of apps'},
  'reading-25':{zh:'深夜时段',en:'Late-night hours'},
  'reading-26':{zh:'上午的深度窗口',en:'Deep work in the morning'},
  'reading-27':{zh:'下午的会议带',en:'Afternoon meeting band'},
  'reading-28':{zh:'注意力预算',en:'Attention budget'},
  'reading-29':{zh:'多任务错觉',en:'The multitasking illusion'},
  'reading-30':{zh:'单任务回返',en:'Single-task returns'},
  'reading-31':{zh:'停留时长会骗人',en:'Dwell time can mislead'},
  'reading-32':{zh:'开合节奏',en:'Open-close rhythm'},
  'reading-33':{zh:'每周回看一次',en:'A weekly review ritual'},
  'reading-34':{zh:'对比上周',en:'Compare to last week'},
  'reading-35':{zh:'阈值与噪声',en:'Thresholds and noise'},
  'reading-36':{zh:'样本不足时',en:'When samples are thin'},
  'reading-37':{zh:'问题比答案重要',en:'Questions beat answers'},
  'reading-38':{zh:'行动要小而具体',en:'Keep actions small'},
  'reading-39':{zh:'情绪与数值分开',en:'Keep mood and numbers apart'},
  'reading-40':{zh:'隐私默认本地',en:'Privacy stays local by default'},
  'reading-41':{zh:'聚合而非内容',en:'Aggregates, not content'},
  'reading-42':{zh:'自我叙事的素材',en:'Material for your own story'},
  'reading-43':{zh:'习惯的可见性',en:'Making habits visible'},
  'reading-44':{zh:'变化的加速度',en:'The pace of change'},
  'reading-45':{zh:'环境胜过意志',en:'Environment over willpower'},
  'reading-46':{zh:'设置意图',en:'Set an intention'},
  'reading-47':{zh:'回顾时不要自责',en:'Review without blame'},
  'reading-48':{zh:'小步验证',en:'Small-step verification'},
  'reading-49':{zh:'数据也有生命周期',en:'Data has a lifecycle'},
  'reading-50':{zh:'长期主义',en:'The long view'},
};

var AGENT_READING_PARAGRAPHS=[
  {
    id:'reading-01',
    zh:'Choe 等人在 CHI 2014 年发表的《Understanding Quantified-Selfers’ Practices》中访谈了 52 位资深量化自我实践者，发现一个反复出现的规律：数据采集本身并不等于反思。真正产生价值的是“与数据一起生活”——把记录拿回来、反复探索、串联成可解释的叙事。因此，回看这一步不是可有可无的补充，而是整个系统里最关键的环节。',
    en:'In their CHI 2014 study of 52 experienced self-trackers, Choe et al. found a recurring pattern: collecting data is not the same as reflecting. Value emerges only when people “live with the data” — bringing records back, exploring them repeatedly, and connecting them into an interpretable narrative. Review is therefore not an optional extra but the most critical step in the whole system.'
  },
  {
    id:'reading-02',
    zh:'Baumer 等人在 CHI 2013 年的论文里专门厘清了“反思”这个概念：它不是灵光一现的顿悟，而是对自身体验的检视、比较与再审视。按这个定义，反思的重点在于你如何描述和比较自己的过去，而不是等待一个突然的答案。所以这里给出的参照都是你自己近期的范围，而不是任何绝对标准。',
    en:'In a CHI 2013 paper, Baumer et al. carefully define reflection: not a sudden flash of insight, but the examination, comparison, and re-examination of one’s own experience. By this definition, reflection depends on how you describe and compare your past, not on waiting for an abrupt answer. That is why the references here are drawn from your own recent range rather than any absolute standard.'
  },
  {
    id:'reading-03',
    zh:'Smit 等人在 2023 年的研究中比较了不同记录负担的追踪方式，结论是：低负担、可回看的少量指标，比面面俱到的记录更有利于长期反思。因为当记录本身消耗过多注意力时，人们很快就会放弃，或者只是机械地填写。保留少量聚合指标，正是为了让“回看”这件事可以持续发生。',
    en:'Smit et al. (2023) compared tracking approaches with different recording burdens and concluded that lightweight, reviewable measures support long-term reflection better than exhaustive records. When the act of recording demands too much attention, people either quit quickly or fill in mechanically. Keeping a small set of aggregates is precisely what lets the review itself keep happening.'
  },
  {
    id:'reading-04',
    zh:'Li、Dey 与 Forlizzi 在 CHI 2010 年提出的阶段模型，把个人信息系统拆成准备、采集、整合、反思、行动五个阶段，并用访谈表明：任何一个阶段卡住，整条链都会失效。其中最常见的问题不是人们不想反思，而是数据散落各处、格式混乱，导致“整合”这一步过重。换句话说，洞察的前提是数据先被整理成可读、可比的形式。',
    en:'Li, Dey, and Forlizzi’s CHI 2010 stage model breaks personal informatics into preparation, collection, integration, reflection, and action, showing through interviews that a bottleneck at any stage breaks the whole chain. The most common failure is not a lack of desire to reflect but scattered, messy data that makes integration too costly. In other words, insight presupposes data that has already been shaped into something readable and comparable.'
  },
  {
    id:'reading-05',
    zh:'Feustel 等人在 IMWUT 2018 年的《People Like Me》中研究了一种常见设计：把个人数据与“相似人群”做聚合对比。他们发现对比能显著激发反思，但也容易被读成优劣、焦虑甚至自我责备；同一份对比数据，有人用来校准，有人用来否定自己。本工具因此完全不做与他人的比较，只显示你自己近期的典型范围。',
    en:'Feustel et al.’s IMWUT 2018 paper “People Like Me” studies a common design: comparing personal data with aggregates from “similar people.” They found comparison strongly provokes reflection, yet it is easily read as ranking, anxiety, or self-blame; the same comparison helps some people calibrate and leads others to condemn themselves. This tool therefore never compares you with others; it only shows your own recent typical range.'
  },
  {
    id:'reading-06',
    zh:'Cho 等人在 CHI 2022 年的论文中，把 HCI 关于反思的理论与市面个人信息应用的实际功能做了对照，发现理论与实践之间存在明显缺口：多数应用只展示数据，很少引导用户真正思考。这也提醒我们，一个数值落在常见范围内，并不代表那一天的感觉和状态也相同。数据描述的是行为形状，感受需要你自己补全。',
    en:'In a CHI 2022 paper, Cho et al. contrast theories of reflection with what personal informatics apps actually do, finding a clear gap: most apps display data but rarely guide real thinking. This also reminds us that a value within the usual range does not mean the day felt the same. Data describes the shape of behavior; the feeling is for you to fill in.'
  },
  {
    id:'reading-07',
    zh:'单次停留时间很容易被各种因素拉长或缩短，比如把应用挂着没关。相比之下，回返——一天里反复回到同一个应用——更能说明一件事是否真正占据了你。你可以把回返次数当作比单次时长更可靠的投入信号。',
    en:'A single dwell time can easily be inflated or deflated by factors like leaving an app open. By contrast, returns — coming back to the same app repeatedly during a day — reveal more about what truly occupied you. Treat return count as a more reliable signal of engagement than any single session.'
  },
  {
    id:'reading-08',
    zh:'注意力不是可以无限供应的燃料，一天里的深度时段通常只有少数几段，而且大多集中在某些固定时段。与其要求自己整天高效，不如先找出自己的深度窗口落在哪里，再把重要工作放进去。记录的价值之一，就是让这些窗口变得可见。',
    en:'Attention is not an endlessly renewable fuel; deep windows usually occur only a few times a day, and they tend to cluster in certain hours. Rather than demanding all-day efficiency, first find where your own deep windows fall, then place important work there. One value of tracking is making those windows visible.'
  },
  {
    id:'reading-09',
    zh:'从一件事切到另一件事，大脑需要时间重新进入状态，这个“重入成本”在频繁切换时会被不断重复支付。观察应用之间的切换频率，可以看到你的一天被切碎到什么程度。如果切碎明显，减少切换点通常比延长单次时长更有效。',
    en:'Switching from one task to another costs time as the mind re-enters a state, and this re-entry cost is paid repeatedly under frequent switching. Watching how often you switch between apps reveals how fragmented your day has become. If fragmentation is high, reducing the number of switch points usually beats extending any single session.'
  },
  {
    id:'reading-10',
    zh:'同样的一个小时，放在上午和放在深夜，意义完全不同：上午往往是深度工作的窗口，深夜则常常与恢复或拖延有关。只看全天总量会把这些差异抹平。把数据按时间段拆开看，才能发现真正属于你的节律。',
    en:'The same hour means something very different in the morning than late at night: mornings are often deep-work windows, while late nights are frequently tied to recovery or procrastination. Daily totals smooth all of this away. Reading data by time of day is the only way to find the rhythm that is actually yours.'
  },
  {
    id:'reading-11',
    zh:'偶尔漏记、偶尔忘记关掉计时器，并不会破坏整体判断。真正影响可信度的是覆盖是否连续：断断续续的记录会让趋势失真。所以不必追求完美，只要保持稳定、连续的覆盖，数据就足够可信。',
    en:'An occasional miss or a forgotten timer will not ruin the overall picture. What actually matters for trustworthiness is continuity of coverage: patchy records distort trends. Do not aim for perfection; stable, continuous coverage is enough.'
  },
  {
    id:'reading-12',
    zh:'单日数据的波动很大，情绪、任务、偶发事件都会造成干扰，单看一天很难得到稳定结论。把回看周期拉长到一周，工作与休息的真实结构才会浮现。固定每周回看一次，比随时刷新更能沉淀出结构感。',
    en:'Daily data swings widely; mood, tasks, and one-off events all add noise, so a single day rarely yields a stable conclusion. Stretching the review cycle to a week lets the real structure of work and rest emerge. A fixed weekly review settles into structure better than constant refreshing.'
  },
  {
    id:'reading-13',
    zh:'这份数据只描述你的使用形状：什么时候在用、用了多久、哪些应用占据前台。它不评价好坏，也不替你下结论。解读权始终在你手里——镜子只负责照出形状，判断由你做出。',
    en:'This data only describes the shape of your usage: when you were active, for how long, and which apps held the foreground. It does not grade you or draw conclusions for you. The interpretation always stays with you — a mirror shows the shape; the judgment is yours.'
  },
  {
    id:'reading-14',
    zh:'所谓“典型范围”或“个人参考”，需要积累足够多的有效天数才有意义。头几天的数值只能算初步可比，过早解读容易把噪声当信号。先让覆盖稳定下来，再谈变化。',
    en:'A “typical range” or personal baseline only becomes meaningful after enough valid days accumulate. Early numbers are only preliminarily comparable, and premature reading easily mistakes noise for signal. Stabilize coverage first, then talk about change.'
  },
  {
    id:'reading-15',
    zh:'解读变化时，先看方向和幅度，再看是否连续出现。孤立一天的偏移，可能只是临时任务或环境干扰，通常不值得过度解读。连续多日同向的变化，才更可能反映了真实的节律改变。',
    en:'When reading a change, look at direction and size first, then at whether it persists. An isolated shift may be a temporary task or environmental disturbance and rarely deserves over-interpretation. Only changes that persist for several days in the same direction are likely to reflect a real shift in rhythm.'
  },
  {
    id:'reading-16',
    zh:'一段明显安静的时间，和高峰一样值得注意：它可能意味着恢复和蓄力，也可能意味着停滞或逃避。区别两者的关键，是结合前后几天的上下文来看，而不是孤立地评价某一天。',
    en:'An unusually quiet stretch is as informative as a peak: it can mean recovery and recharging, or stagnation and avoidance. The key to telling them apart is reading the surrounding days for context rather than judging any single day in isolation.'
  },
  {
    id:'reading-17',
    zh:'停留时间最长的应用只是你的“主场”，它说明你在哪里待得久，却不说明产出如何。有人把大量时间花在阅读和浏览上，依然收获颇多；也有人整天开着编辑器却很少推进。别把“用得久”直接读成“做得好”。',
    en:'The app you dwell in longest is only your main stage; it shows where you spend time, not what you produce. Some people spend hours reading and browsing and gain a great deal; others keep an editor open all day and barely advance. Do not read “used longest” as “done best.”'
  },
  {
    id:'reading-18',
    zh:'键盘活跃只说明输入事件在发生，可能是打字，也可能是无意识的敲击或游戏操作。专注是一种更深层、更难观察的状态，任何输入指标都只能提供线索，不能直接下结论。把键盘数据当作方向提示，而不是事实判定。',
    en:'Keyboard activity only means input events are occurring — it could be typing, absent-minded keystrokes, or gaming. Focus is a deeper state that is far harder to observe; any input metric can only offer clues, never verdicts. Treat keyboard data as a directional hint, not a factual judgment.'
  },
  {
    id:'reading-19',
    zh:'浏览、阅读类应用同样参与你的认知生活：查找资料、阅读长文、收集灵感，都是真实的工作形式。强度榜只记录时间分布，不做道德评判。给“看起来没在干活”的时间留出解释空间，往往能发现意想不到的价值。',
    en:'Browsing and reading apps are also part of your cognitive life: researching, reading long-form content, and collecting ideas are real forms of work. The strength list records time distribution; it does not moralize. Leaving room to interpret “seemingly unproductive” time often uncovers unexpected value.'
  },
  {
    id:'reading-20',
    zh:'离开电脑的时间，塑造了你回来之后的手感：休息充分，回返才可能有质量。因此记录里那些安静的空档不是“空白”，而是节律的另一半。看节律时，别忘了把休息也算进去。',
    en:'Time away from the computer shapes the touch you bring back: with proper rest, returns can have real quality. The quiet gaps in the record are therefore not “emptiness” but the other half of your rhythm. When reading rhythm, remember to count rest too.'
  },
  {
    id:'reading-21',
    zh:'长时间高强度输入之后出现一段安静窗口，是相当常见的节律模式。它往往不是“浪费”，而是恢复——大脑需要时间整合刚才的工作。如果每次峰值后都有类似的回落，这更像是健康的节奏，而不是怠惰。',
    en:'A quiet window after a long stretch of intense input is a common rhythm. It is usually recovery, not waste — the mind needs time to integrate what was just done. If a similar dip follows every peak, it is more likely a healthy rhythm than laziness.'
  },
  {
    id:'reading-22',
    zh:'日视图回答“今天怎么过的”，适合回顾具体的一天；周视图回答“这周在往哪走”，适合看趋势与结构。两个尺度各有用途，只看其一都会失真。把日与周结合起来，才能既看见细节，又看见方向。',
    en:'The day view answers “how did today go” and suits recalling a specific day; the week view answers “where is this week heading” and suits trends and structure. Each scale has its own purpose, and either one alone distorts the picture. Combining day and week shows both detail and direction.'
  },
  {
    id:'reading-23',
    zh:'只有连续记录，回看才有可靠的参照；中断越多，你越难判断某天的变化是真实还是记录缺口造成的。连续覆盖的价值，不只是数据更完整，更是让你在解读时更敢相信自己的判断。',
    en:'Only continuous records give you a reliable reference; the more gaps, the harder it is to tell whether a change is real or an artifact of missing data. Steady coverage matters not just because the data is fuller, but because it lets you trust your own reading.'
  },
  {
    id:'reading-24',
    zh:'绝大多数应用每天只出现几分钟，合起来却占据了可观的注意力。真正的结构与偏好，往往藏在少数几个高频应用里，但长尾也值得偶尔看一眼——它可能暴露你没有意识到的习惯性打开。',
    en:'Most apps appear for only a few minutes a day, yet together they consume a noticeable share of attention. The real structure usually hides in a few high-frequency apps, but the long tail is worth a glance now and then — it may expose habitual openings you never noticed.'
  },
  {
    id:'reading-25',
    zh:'深夜仍在使用高强度应用，通常是节律偏移的信号：它可能意味着白天被占满、晚上才开始真正工作，也可能是作息在缓慢后移。先看见这个模式，再决定是否要调整，而不是直接否定它。',
    en:'Heavy use of intense apps late at night is often a sign of rhythm drift: the day may be so full that real work only starts at night, or the sleep schedule may be slowly shifting later. See the pattern first, then decide whether to adjust it, rather than condemning it outright.'
  },
  {
    id:'reading-26',
    zh:'许多人的深度窗口集中在上午，起床后的一两小时尤其适合需要专注的工作。如果你的数据也呈现这种分布，把最重要的任务放进这段时间，通常会顺畅很多。关键是先确认你自己的窗口在哪，而不是照搬别人的时间表。',
    en:'Many people’s deep windows cluster in the morning, with the first hour or two after waking especially suited to focused work. If your data shows the same distribution, scheduling your most important tasks there usually flows much better. The key is confirming where your own window falls, not copying someone else’s schedule.'
  },
  {
    id:'reading-27',
    zh:'下午时段常常出现碎片化的应用切换：消息、文档、浏览器来回跳动。这未必是效率问题，而是协作型工作的正常形状。与其对抗这种碎片，不如把下午留给需要响应和沟通的事情，把深度工作留在上午。',
    en:'Afternoons often show fragmented switching: messages, documents, and browsers jumping back and forth. This is not necessarily a productivity problem; it is the normal shape of collaborative work. Rather than fighting the fragmentation, leave afternoons for responsive, communicative tasks and keep deep work in the morning.'
  },
  {
    id:'reading-28',
    zh:'把一天想象成一份注意力预算：每个应用都在从中支取，而预算总量有限。回看时，你会更容易说清楚时间花在了哪里、哪些支取是主动的、哪些只是惯性。预算思维的价值，是让选择重新变得可见。',
    en:'Think of a day as an attention budget: every app withdraws from it, and the total is limited. Reviewing makes it easier to say where the time went, which withdrawals were deliberate, and which were just habit. The value of budget thinking is that it makes choices visible again.'
  },
  {
    id:'reading-29',
    zh:'所谓同时做几件事，在数据里几乎总是表现为快速切换：几秒前在文档，几秒后在聊天，再切回来。大脑并非真正并行，而是在反复付出重入成本。记录会诚实地把这种切碎显示出来，让你看清“多任务”的真实代价。',
    en:'What feels like doing several things at once almost always appears in data as rapid switching: seconds in a document, seconds in chat, then back again. The mind is not truly parallel; it keeps paying re-entry costs. The record honestly shows this fragmentation so you can see the real price of “multitasking.”'
  },
  {
    id:'reading-30',
    zh:'在一个应用上反复回返、停留逐渐加深，往往意味着真正的投入——你一次次回到它，说明它承载着你的核心工作。相比之下，一次性停留很久可能只是挂着。回返次数比单次时长更可靠，也更难伪装。',
    en:'Deepening, repeated returns to one app usually mark real engagement — coming back to it again and again suggests it carries your core work. A single long dwell, by contrast, may just mean leaving it open. Return count is more reliable than any single session, and harder to fake.'
  },
  {
    id:'reading-31',
    zh:'停留时间长，可能是沉浸，也可能是把应用挂着没管；两种情况的含义完全不同。同样，停留短可能是快速查一下，也可能是被打断。把停留时长与回返次数、所在时段放在一起看，才更接近真相。',
    en:'Long dwell can mean immersion, or it can simply mean leaving the app open; the two have opposite meanings. Likewise, a short dwell may be a quick lookup or an interruption. Only by pairing dwell time with return count and time of day can you get closer to the truth.'
  },
  {
    id:'reading-32',
    zh:'一天里应用开开关关的节奏，像呼吸：开合太密说明频繁被打断或在不同任务间跳跃，开合太疏则可能说明长时间离线或长时间泡在单一应用里。这个节奏本身，就是一天流畅与否的一个侧面。',
    en:'The open-and-close rhythm of your day is like breathing: too dense means frequent interruption or hopping between tasks, while too sparse may mean long stretches offline or absorbed in a single app. The rhythm itself is one facet of how smoothly the day flowed.'
  },
  {
    id:'reading-33',
    zh:'每周固定回看一次，比随时刷新更能沉淀出结构感：固定的仪式会让“看数据”成为稳定习惯，而不是焦虑时的一瞥。把回看放进日程，比如每周日晚，让它变成一件轻松、可预期的小事。',
    en:'A fixed weekly review settles into structure better than constant refreshing: a regular ritual turns “looking at data” into a stable habit rather than an anxious glance. Put the review in your schedule — say, Sunday evening — and make it a light, predictable routine.'
  },
  {
    id:'reading-34',
    zh:'与上周对比时，留意的是趋势而不是好坏：这一周是在往某个方向移动，还是只是随机波动。连续两周同向的变化才值得认真对待，单周的差异往往只是噪声。趋势判断需要时间尺度，而不是单点比较。',
    en:'When comparing with last week, watch trends rather than good or bad: is the week moving in some direction, or just fluctuating randomly? Only two consecutive weeks in the same direction deserve serious attention; a single week’s difference is usually noise. Trend judgment needs a time scale, not a single-point comparison.'
  },
  {
    id:'reading-35',
    zh:'单日数值天然带有噪声：临时任务、状态起伏都会造成波动。只有越过个人阈值、并且连续出现的变化，才值得进入回想。给“变化”设置一个门槛，可以避免把日常波动当成重大信号。',
    en:'Daily numbers are naturally noisy: temporary tasks and mood swings all create fluctuation. Only changes that cross your personal threshold and persist deserve reflection. Setting a bar for “change” prevents you from mistaking ordinary fluctuation for a major signal.'
  },
  {
    id:'reading-36',
    zh:'记录天数不足时，任何关于趋势的结论都只是猜测：两三天看不出方向，一周也才刚刚起步。这时最该做的不是解读，而是把覆盖做稳。先积累足够的有效天数，再谈“典型范围”和“变化”。',
    en:'With too few recorded days, any conclusion about trends is guesswork: two or three days show no direction, and even a week is just a start. The right move is not interpretation but stabilizing coverage. Accumulate enough valid days first, then talk about “typical ranges” and “change.”'
  },
  {
    id:'reading-37',
    zh:'回想的目标不是给一天打分，而是提出好问题：为什么这天特别长？是什么在反复吸引我？这些问题的价值在于把你带回当天的情境，让记忆和上下文重新参与解读。数据给出形状，问题负责找回意义。',
    en:'The goal of recall is not to score the day but to ask good questions: why was this day so long? What keeps pulling me back? Their value is bringing you back into the day’s context so memory can rejoin the interpretation. Data provides the shape; questions recover the meaning.'
  },
  {
    id:'reading-38',
    zh:'宏大的改进计划往往在第一天就失败，因为它们没有落点。把建议变成小到明天就能做的动作——比如“上午十点前不开聊天软件”或“午饭后下楼走十分钟”——执行率会高得多。小步验证，比一次性大改更可靠。',
    en:'Grand improvement plans usually fail on day one because they have no concrete landing point. Turning a suggestion into an action small enough for tomorrow — “no chat apps before 10 am” or “a ten-minute walk after lunch” — raises the chance it actually happens. Small steps verified one at a time beat one big overhaul.'
  },
  {
    id:'reading-39',
    zh:'数值描述行为，不描述感受：同一天可能数据平淡但心情很糟，也可能数据密集但状态轻松。两种读法可能同时成立，不必让其中一个压过另一个。记录行为，情绪留给回忆和觉察。',
    en:'Numbers describe behavior, not feeling: a day can be flat in data yet bad in mood, or dense in data yet relaxed. Both readings can be true at once, so let neither dominate. Record the behavior; leave the emotion to memory and awareness.'
  },
  {
    id:'reading-40',
    zh:'所有原始数据默认只留在本机，包括窗口标题、输入内容和坐标，都不会被发送。只有在你明确填写 API Key 之后，聚合字段——时长、输入事件数、应用停留——才会发送给所选服务。不填 Key，就完全不发生任何出站请求。',
    en:'All raw data stays on this machine by default, including window titles, typed content, and coordinates, none of which are ever sent. Only after you explicitly add an API Key are aggregate fields — duration, input event counts, app dwell — sent to your chosen service. Without a Key, no outbound request ever happens.'
  },
  {
    id:'reading-41',
    zh:'这里记录的是时长、键鼠响应和应用停留等聚合字段，不读取你输入了什么、不保存窗口标题，也不涉及正文。隐私边界由设计保证，而不是靠承诺。这意味着即使数据被查看，也无法还原你的具体内容。',
    en:'This records aggregates — duration, keyboard/mouse responses, and app dwell — and never reads what you type, never stores window titles, and never touches content. The privacy boundary is guaranteed by design, not by promise. Even if the data were inspected, your actual content could not be reconstructed.'
  },
  {
    id:'reading-42',
    zh:'数据是素材，不是结论：同一周的数据，可以被读成“我很忙”，也可以被读成“我需要更多休息”。把它当作书写自己一周叙事的原料，而不是审判你的裁判。你才是那个决定故事走向的人。',
    en:'Data is material, not verdict: the same week can be read as “I was busy” or as “I need more rest.” Treat it as raw material for narrating your own week, not as a judge passing sentence. You are the one who decides where the story goes.'
  },
  {
    id:'reading-43',
    zh:'习惯之所以顽固，很大程度上是因为看不见：无意识的打开、惯性般的切换，都发生在注意力的盲区里。把使用形状画出来，本身就是一次干预——一旦看得见，可塑性就出现了。数据的作用首先是照亮，然后才是改变。',
    en:'Habits are stubborn largely because they are invisible: unconscious openings and habitual switches happen in the blind spots of attention. Drawing the shape of your usage is itself an intervention — once something is visible, it becomes malleable. Data works first by illuminating, then by enabling change.'
  },
  {
    id:'reading-44',
    zh:'节律的改变很少一夜发生，大多是缓慢漂移：每天后移十分钟，一个月就后移五个小时，但每天都察觉不到。回看较长的时间区间，才能看见这种加速度和方向。短窗口看不到漂移，长窗口才看得到趋势。',
    en:'Rhythm rarely changes overnight; it drifts slowly. Ten minutes later each day becomes five hours in a month, yet no single day reveals it. Only longer ranges show this pace and direction: short windows hide the drift, long windows reveal the trend.'
  },
  {
    id:'reading-45',
    zh:'与其反复告诫自己“少刷手机”，不如调整环境：把高频应用移出主屏、关掉非必要通知、给专注时段设置物理边界。环境设计比意志力可靠得多，因为它在你松懈的时候仍然起作用。记录能帮你找到最该调整的那个环境因素。',
    en:'Rather than repeatedly telling yourself to scroll less, adjust the environment: move high-frequency apps off the home screen, silence non-essential notifications, and set physical boundaries for focus time. Environmental design beats willpower because it keeps working when you slack off. The record helps you find which environmental factor to change first.'
  },
  {
    id:'reading-46',
    zh:'在开始一天之前设一个意图，比如“上午只做一件事”或“午饭前不看消息”，会给当天一个可以对照的锚点。晚上回看时，你可以问：我有没有守住这个意图？没有的话，是被什么带走的？意图让数据从“事后描述”变成“事前约定”。',
    en:'Setting an intention before the day — “one thing in the morning” or “no messages before lunch” — gives the day an anchor to check against. In the evening review, ask: did I hold the intention? If not, what carried me away? Intentions turn data from an after-the-fact description into a before-the-fact agreement.'
  },
  {
    id:'reading-47',
    zh:'回看时把自己当成观察者而不是被告：自责会关闭洞察，让你只想辩护或逃避；好奇才会打开它，让你想弄清楚“为什么会这样”。同一份数据，在两种心态下会读到完全不同的东西。选择好奇，而不是审判。',
    en:'Review as an observer, not a defendant: blame closes insight and makes you want to defend or escape, while curiosity opens it and makes you ask “why did this happen.” The same data reads completely differently under the two mindsets. Choose curiosity over judgment.'
  },
  {
    id:'reading-48',
    zh:'每次只调整一件事，用一到两周的数据验证效果：比如“这周把午休去掉”或“这周十点前不开聊天软件”。同时改太多，就说不清是哪一步起了作用。单变量实验虽然朴素，却是把“感觉有用”变成“确实有用”最可靠的方式。',
    en:'Change one thing at a time and verify it with one or two weeks of data — say, “skip the nap this week” or “no chat apps before ten.” Changing too much at once blurs which step worked. Single-variable experiments are plain, but they are the most reliable way to turn “feels useful” into “actually works.”'
  },
  {
    id:'reading-49',
    zh:'数据会过时：一个月前的节律参考价值在递减，半年前的结论可能已经不再成立。定期清理旧解读、只保留仍然成立的结论，可以让你的记录保持新鲜和可信。数据不是越攒越多越好，而是越及时越有用。',
    en:'Data ages: a month-old rhythm reference is already losing value, and conclusions from six months ago may no longer hold. Cleaning out old readings regularly and keeping only what still holds keeps your records fresh and trustworthy. More data is not automatically better; timely data is what is useful.'
  },
  {
    id:'reading-50',
    zh:'手感不是一天形成的，也不会一天改变：它是一周、一月、一季的累积结果。把回看的尺度拉长到月和季，才能看到真正属于你自己的节律，而不是被某几天的波动带着走。长期视角，是让数据最终服务于你的关键。',
    en:'Your touch is not formed in a day and will not change in a day; it is the accumulated result of weeks, months, and seasons. Stretching the review scale to months and seasons lets you see the rhythm that is truly yours instead of being carried by a few days’ fluctuation. The long view is what finally makes data serve you.'
  },
];



var AGENT_READING_FULLTEXT={
  'stage-based-personal-informatics':{
    zh:[
      {h:'背景',b:'Li、Dey 与 Forlizzi 在 CHI 2010 发表的《A Stage-Based Model of Personal Informatics Systems》提出：个人信息系统（如计步器、睡眠记录、时间追踪）不是单一工具，而是一条包含多个阶段的链条。'},
      {h:'方法与对象',b:'研究通过对 22 位使用过多种个人信息系统的参与者进行访谈，归纳出“准备、采集、整合、反思、行动”五个阶段，并逐阶段记录人们遇到的障碍。'},
      {h:'主要发现',b:'每个阶段都有各自的障碍（例如采集阶段工具太繁琐、整合阶段数据散落多处、反思阶段缺少可视化），任何一个环节卡住，整个系统就失效；人们放弃工具，往往不是因为不想记录，而是因为某个中间阶段太费力。'},
      {h:'局限与边界',b:'该模型来自早期工具与访谈，描述的是典型路径；它不证明任何工具能带来行为改变，也不对“使用时长等于产出”做因果判断。'},
      {h:'对本工具的启示',b:'本工具把“采集”压缩到系统后台自动完成，只保留聚合字段，并优先保证“整合”阶段的稳定覆盖——因为模型提醒我们，反思要发生，前提是数据先被整理成可读、可比的形式。'},
    ],
    en:[
      {h:'Background',b:'Published at CHI 2010, “A Stage-Based Model of Personal Informatics Systems” by Li, Dey, and Forlizzi argues that personal informatics tools (pedometers, sleep logs, time trackers) are not single instruments but chains of stages.'},
      {h:'Method and participants',b:'Based on interviews with 22 participants who used a range of personal informatics systems, the authors derived five stages — preparation, collection, integration, reflection, and action — and recorded the barriers people met at each one.'},
      {h:'Key findings',b:'Each stage has its own barriers (tedious collection, data scattered across sources, weak visualization for reflection), and a bottleneck anywhere breaks the whole loop. People abandon tools not because they do not want to track, but because an intermediate stage demands too much effort.'},
      {h:'Limits and boundaries',b:'The model derives from early tools and interviews; it describes a typical path but does not prove any tool changes behavior, nor does it equate usage time with output.'},
      {h:'What this tool takes from it',b:'This tool pushes collection into the background automatically, keeps only aggregates, and prioritizes stable coverage at the integration stage — because, as the model reminds us, reflection can only begin once data has been shaped into something readable and comparable.'},
    ],
  },
  'quantified-self-collection-exploration':{
    zh:[
      {h:'背景',b:'Choe、Lee、Lee、Pratt 与 Kientz 在 CHI 2014 发表的《Understanding Quantified-Selfers’ Practices in Collecting and Exploring Personal Data》聚焦量化自我社区里最有经验的实践者，想弄清：他们为什么能长期坚持，又为什么能从中获益。'},
      {h:'方法与对象',b:'研究者访谈了 52 位量化自我实践者，收集他们使用的工具、记录方式与回看习惯，并按“采集—探索”两个环节分析这些实践。'},
      {h:'主要发现',b:'核心发现是：采集本身并不产生价值，价值来自“与数据一起生活”——反复探索、串联、把数据带进日常情境去解释；而许多新手失败，正是因为他们只采集不探索，或数据格式混乱导致探索成本过高。'},
      {h:'局限与边界',b:'访谈对象是高度投入的资深实践者，样本偏向对数据有强烈兴趣的人群；结论描述实践模式，不证明某个具体工具的效果。'},
      {h:'对本工具的启示',b:'本工具刻意只保留少量聚合字段，并把回看界面做得轻、快、可翻页，就是希望降低“探索”这一步的成本，让采集之后的那一步真正发生。'},
    ],
    en:[
      {h:'Background',b:'“Understanding Quantified-Selfers’ Practices in Collecting and Exploring Personal Data” (Choe, Lee, Lee, Pratt, Kientz; CHI 2014) focuses on the most experienced members of the quantified-self community to ask why they persist and how they benefit.'},
      {h:'Method and participants',b:'The authors interviewed 52 self-trackers, cataloguing their tools, recording habits, and review practices, then analyzed them along the two phases of collecting and exploring.'},
      {h:'Key findings',b:'Collection alone creates little value; value comes from “living with data” — repeatedly exploring, connecting, and bringing records into everyday contexts to interpret them. Many beginners fail not because they do not track, but because messy formats make exploration too costly.'},
      {h:'Limits and boundaries',b:'The interviewees were deeply engaged, experienced trackers, so the sample skews toward people with strong interest in data; the findings describe practices rather than proving any tool’s effect.'},
      {h:'What this tool takes from it',b:'This tool deliberately keeps only a few aggregate fields and makes review light, fast, and pageable — lowering the cost of the exploration step so that the step after collection can actually happen.'},
    ],
  },
  'flexible-minimalist-self-tracking':{
    zh:[
      {h:'背景',b:'Smit 等人 2023 年发表的《Flexible Minimalist Self-Tracking to Support Individual Reflection》考察了一类被主流工具忽略的实践：只用笔、纸、简单表格等低技术方式做少量记录的人。'},
      {h:'方法与对象',b:'研究者访谈了这类“轻量追踪者”，分析他们为什么放弃复杂工具、又如何让少量指标支撑起持续的自我反思。'},
      {h:'主要发现',b:'灵活性（想记就记、形式可改）与低负担（指标少、维护成本低）是这些人能长期坚持的关键；与之相对，功能全但负担重的工具反而更快被放弃。'},
      {h:'局限与边界',b:'样本规模有限且来自特定文化背景；研究支持“轻量有利于反思”这一取向，但不否认某些场景需要更详细的记录。'},
      {h:'对本工具的启示',b:'本工具的指标刻意少而稳定：时长、键鼠、应用停留与回返，都是不需要额外动作就能长期维护的字段——这正是“轻量而可回看”的设计取向。'},
    ],
    en:[
      {h:'Background',b:'“Flexible Minimalist Self-Tracking to Support Individual Reflection” (Smit et al., 2023) examines a practice overlooked by mainstream tools: people who track little, using low-tech methods such as pen, paper, and simple tables.'},
      {h:'Method and participants',b:'The authors interviewed such lightweight trackers, analyzing why they abandoned complex tools and how a few measures still supported sustained self-reflection.'},
      {h:'Key findings',b:'Flexibility (record when you like, change the form freely) and low burden (few measures, little maintenance) are the keys to persistence; conversely, feature-rich but heavy tools are abandoned sooner.'},
      {h:'Limits and boundaries',b:'The sample is small and culturally specific; the study supports lightweight tracking for reflection but does not deny that some situations call for richer records.'},
      {h:'What this tool takes from it',b:'This tool deliberately keeps measures few and stable — time, keys, mouse, app dwell, and returns — fields that need no extra effort to maintain over the long term. That is the “lightweight and reviewable” design stance.'},
    ],
  },
  'reflection-nature-critical-examination':{
    zh:[
      {h:'背景',b:'Baumer 等人在 CHI 2013 的《Personal Informatics and Reflection: A Critical Examination of the Nature of Reflection》中发现，“反思”在 HCI 中被大量使用，却很少被定义清楚。'},
      {h:'方法与对象',b:'研究者对 HCI 文献中“反思”的用法做了批判性梳理，区分了作为结果的反思（突然顿悟）与作为过程的反思（检视与比较），并考察了背后的理论传统。'},
      {h:'主要发现',b:'他们主张把反思理解为一种对经验进行检视、比较与再审视的持续过程，而不是一次性的顿悟；仅靠展示数据并不能自动引发反思，还需要合适的框架与契机。'},
      {h:'局限与边界',b:'该文是概念性、批判性论文，不提供实证效果数据；它的价值在于给出更谨慎的“反思”定义，而非证明某种设计一定有效。'},
      {h:'对本工具的启示',b:'本工具的“回想”刻意使用温和措辞，以“你近期的个人范围”为参照而非绝对标准，并把每次变化当作回忆当时情境的提示——这些设计都源于这一批判性界定。'},
    ],
    en:[
      {h:'Background',b:'In “Personal Informatics and Reflection: A Critical Examination of the Nature of Reflection” (CHI 2013), Baumer et al. observe that “reflection” is invoked everywhere in HCI yet rarely defined.'},
      {h:'Method and scope',b:'The authors critically survey uses of reflection in the HCI literature, distinguishing reflection as an outcome (sudden insight) from reflection as a process (examining and comparing), and tracing the theoretical traditions behind each.'},
      {h:'Key findings',b:'Reflection is better understood as an ongoing process of examining, comparing, and re-examining experience rather than a one-shot insight; merely displaying data does not automatically trigger reflection — it needs a frame and an occasion.'},
      {h:'Limits and boundaries',b:'This is a conceptual, critical paper without empirical effect data; its contribution is a more careful definition of reflection, not proof that any particular design works.'},
      {h:'What this tool takes from it',b:'The “recall” section here uses deliberately gentle wording, references your own recent range rather than an absolute standard, and treats each change as a cue to recall context — all design choices derived from this critical framing.'},
    ],
  },
  'people-like-me-cohort-comparison':{
    zh:[
      {h:'背景',b:'Feustel 等人在 IMWUT 2018 发表的《People Like Me》研究了一个常见但危险的设计选择：把用户数据与“和自己相似的人群”做聚合对比，会怎样影响反思？'},
      {h:'方法与对象',b:'他们设计并部署了一套会向用户展示同侪聚合数据（如睡眠、活动）的原型系统，通过日志与访谈观察参与者如何解读这些对比。'},
      {h:'主要发现',b:'对比能显著激发反思，但也可能被读成优劣与焦虑的来源；同一份对比数据，有人用来校准自我认知，有人用来责备自己。系统设计者需要小心选择对比的基准与措辞。'},
      {h:'局限与边界',b:'研究基于特定原型与参与者群体；结论强调对比的“双刃”属性，而非断言对比本身必然有害或有益。'},
      {h:'对本工具的启示',b:'本工具完全不做与他人的比较，只显示你自己近期的典型范围；这正是为了避免把“相对位置”误读成“好坏”或“落后”。'},
    ],
    en:[
      {h:'Background',b:'“People Like Me: Designing for Reflection on Aggregate Cohort Data in Personal Informatics Systems” (Feustel et al., IMWUT 2018) investigates a common yet risky design choice: how does showing users aggregate comparisons with “people like me” shape reflection?'},
      {h:'Method and participants',b:'The authors built and deployed a prototype that surfaced peer-aggregate data (e.g., sleep, activity) and used logs plus interviews to see how participants interpreted the comparisons.'},
      {h:'Key findings',b:'Comparison strongly provokes reflection, but it can also be read as ranking and anxiety; the same comparison helps some users calibrate self-perception and leads others to blame themselves. Designers must choose the baseline and wording with care.'},
      {h:'Limits and boundaries',b:'The study rests on one prototype and a specific participant group; its conclusion is that comparison cuts both ways, not that it is inherently harmful or helpful.'},
      {h:'What this tool takes from it',b:'This tool never compares you with other people; it only shows your own recent typical range. That is a deliberate choice to keep “relative position” from being misread as better/worse or behind/ahead.'},
    ],
  },
  'reflection-theory-practice-gaps':{
    zh:[
      {h:'背景',b:'Cho、Xu、Zimmermann-Niefield 与 Voida 在 CHI 2022 的论文中，把 HCI 关于反思的理论与市面个人信息类应用的实际功能做了对照。'},
      {h:'方法与对象',b:'研究者对大量个人信息类应用进行了系统的功能盘点与内容分析，评估它们在多大程度上实现了理论上的“反思支持”。'},
      {h:'主要发现',b:'理论与实践之间存在明显缺口：多数应用提供数据展示，却很少提供引导反思的框架、时机或问题；它们把“看得见”当作“想得深”，用户因此停留在浏览层面。'},
      {h:'局限与边界',b:'分析以应用功能为主，不测量长期使用效果；它指出缺口的普遍存在，但未给出唯一的解决方案。'},
      {h:'对本工具的启示',b:'本工具在数据之外加入了“回想问题”与“路径建议”，并在措辞上避免断言因果——这正是为了弥合“展示数据”与“真正反思”之间的那道缺口。'},
    ],
    en:[
      {h:'Background',b:'In a CHI 2022 paper, Cho, Xu, Zimmermann-Niefield, and Voida contrast theories of reflection in HCI with what personal informatics apps actually do in practice.'},
      {h:'Method and scope',b:'The authors systematically inventoried and content-analyzed a large set of personal informatics apps, assessing how far each realized theoretical “reflection support.”'},
      {h:'Key findings',b:'There is a clear gap between theory and practice: most apps display data but offer little framing, timing, or questioning to guide reflection; they treat “seeing” as “thinking deeply,” leaving users at the browsing level.'},
      {h:'Limits and boundaries',b:'The analysis focuses on app features and does not measure long-term effects; it documents the gap widely without prescribing a single fix.'},
      {h:'What this tool takes from it',b:'Beyond raw data, this tool adds recall questions and path suggestions, and its wording avoids causal claims — steps aimed at bridging the gap between displaying data and genuinely reflecting.'},
    ],
  },
};

function defaultAgentPurposes(){
  return [{purpose_id:'understand_personal_rhythm'},{purpose_id:'notice_rhythm_change'}];
}

function currentAgentPurpose(){
  var list=agentPurposeCatalog&&agentPurposeCatalog.length?agentPurposeCatalog:defaultAgentPurposes();
  for(var i=0;i<list.length;i++){
    if(list[i]&&list[i].purpose_id===agentPurposeId)return list[i];
  }
  return list[0]||null;
}

function agentPurposeDisplay(purpose){
  if(!purpose)return {title:'',description:''};
  var lang=getLang()==='en'?'en':'zh';
  var meta=AGENT_PURPOSE_META[purpose.purpose_id]||{};
  var localized=meta[lang]||{};
  return {
    title:localized.title||purpose.title||purpose.purpose_id||'',
    description:localized.description||purpose.description||''
  };
}

function formatAgentDateRange(range){
  if(!range||!range.start||!range.end)return '—';
  return fmtDateLabel(range.start)+' → '+fmtDateLabel(range.end);
}

function agentCoverageValue(feed){
  var facts=(feed&&feed.evidence_pack&&feed.evidence_pack.facts)||[];
  if(!facts.length||!facts[0]||!facts[0].value||typeof facts[0].value!=='object')return null;
  return facts[0].value;
}

function agentBaselineDays(feed){
  var observations=(feed&&feed.observations)||[];
  for(var i=0;i<observations.length;i++){
    var item=observations[i];
    if(item&&item.kind==='reference_position'&&item.n_valid_days!==undefined&&item.n_valid_days!==null){
      return Number(item.n_valid_days)||0;
    }
  }
  var status=(feed&&feed.evidence_pack&&feed.evidence_pack.reference_status)||{};
  var values=Object.keys(status).map(function(metricId){
    return Number(status[metricId]&&status[metricId].n_valid_days||0);
  });
  if(values.length)return Math.max.apply(Math,values);
  return null;
}

function agentBaselineStage(feed){
  var status=(feed&&feed.evidence_pack&&feed.evidence_pack.reference_status)||{};
  var stages=Object.keys(status).map(function(metricId){return status[metricId]&&status[metricId].stage||'';});
  if(stages.indexOf('typical_range')!==-1)return 'typical_range';
  if(stages.indexOf('initial_reference')!==-1)return 'initial_reference';
  if(stages.indexOf('facts_only')!==-1)return 'facts_only';
  return '';
}

function agentBaselineText(feed){
  var days=agentBaselineDays(feed);
  if(days===null)return '—';
  var suffix='';
  var stage=agentBaselineStage(feed);
  if(stage==='facts_only')suffix=t('agentBaselineFactsOnly');
  else if(stage==='initial_reference')suffix=t('agentBaselineInitial');
  else if(stage==='typical_range')suffix=t('agentBaselineTypical');
  return fmtNum(days)+' '+(getLang()==='en'?'days':'天')+suffix;
}

function agentReferenceReadinessText(feed){
  var status=(feed&&feed.evidence_pack&&feed.evidence_pack.reference_status)||{};
  var items=Object.keys(status).map(function(metricId){return status[metricId]||{};});
  if(!items.length)return '';
  var unavailable=items.filter(function(item){return !item.position_available;});
  if(!unavailable.length)return '';
  var days=Math.max.apply(Math,unavailable.map(function(item){return Number(item.n_valid_days||0);}));
  var threshold=Number(unavailable[0].minimum_valid_days_for_position||7);
  if(days<threshold){
    return t('agentReferenceAccumulating').replace('{days}',fmtNum(days)).replace('{threshold}',fmtNum(threshold));
  }
  return t('agentReferencePending');
}

function agentObservationMetaText(observation,feed){
  var parts=[];
  if(observation.kind==='coverage'){
    var coverage=agentCoverageValue(feed);
    if(coverage&&coverage.qualified_days!==undefined&&coverage.observed_days!==undefined){
      parts.push(t('agentSummaryCoverage')+' '+fmtNum(coverage.qualified_days)+'/'+fmtNum(coverage.observed_days));
    }
  }else{
    parts.push(t('agentSummaryValidDays')+' '+fmtNum(Number(observation.n_valid_days||0))+' '+(getLang()==='en'?'days':'天'));
  }
  return parts.join(' · ');
}

function agentHeaderMetaText(){
  if(agentView==='apps'){
    return agentAppsCount>0
      ? (getLang()==='en'?fmtNum(agentAppsCount)+' apps':fmtNum(agentAppsCount)+' 个应用')
      : '';
  }
  var summary=agentObservationSummary();
  if(agentObservationFeed&&agentObservationFeed.ok===false&&agentObservationFeed.error){
    return agentObservationFeed.error;
  }
  if(summary.loading)return t('agentObservationLoading');
  if(!summary.range&&summary.coverage==='—')return '';
  return summary.range+' · '+t('agentSummaryCoverage')+' '+summary.coverage;
}

function agentObservationSummary(){
  var feed=agentObservationFeed||{};
  var range=feed.evidence_pack&&feed.evidence_pack.range;
  var coverage=agentCoverageValue(feed);
  var purpose=currentAgentPurpose();
  return {
    loading:!feed||!feed.ok,
    range:formatAgentDateRange(range),
    coverage:coverage&&coverage.qualified_days!==undefined&&coverage.observed_days!==undefined
      ? fmtNum(coverage.qualified_days)+'/'+fmtNum(coverage.observed_days)
      : '—',
    baseline:agentBaselineText(feed),
    purpose:agentPurposeDisplay(purpose)
  };
}

function renderAgentPurposeSwitch(){
  var host=document.getElementById('agent-purpose-switch');
  var note=document.getElementById('agent-purpose-note');
  if(!host)return;
  var list=agentPurposeCatalog&&agentPurposeCatalog.length?agentPurposeCatalog:defaultAgentPurposes();
  host.innerHTML=list.map(function(item){
    var display=agentPurposeDisplay(item);
    var active=item.purpose_id===agentPurposeId;
    return '<button type="button" class="agent-purpose-btn '+(active?'active':'')+'" data-purpose-id="'+esc(item.purpose_id)+'" aria-pressed="'+(active?'true':'false')+'" title="'+esc(display.description)+'" onclick="setAgentPurpose(this.dataset.purposeId)">'+esc(display.title)+'</button>';
  }).join('');
  if(note){
    var current=currentAgentPurpose();
    note.textContent=current?agentPurposeDisplay(current).description:'';
  }
}

function syncAgentChrome(){
  renderAgentSummary(agentObservationFeed);
}

function renderAgentSummary(feed){
  var host=document.getElementById('agent-summary');
  if(!host)return;
  var summary=agentObservationSummary();
  host.innerHTML='<b>'+esc(summary.range)+'</b><span>'+esc(t('agentSummaryCoverage'))+' '+esc(summary.coverage)+' · '+esc(t('agentSummaryBaseline'))+' '+esc(summary.baseline)+'</span>';
}

function renderAgentWeekCard(feed){
  var host=document.getElementById('agent-week-card');
  if(!host)return;
  var packet=feed&&feed.evidence_pack?feed.evidence_pack:{};
  var summary=packet.week_summary||{};
  if(!summary.days){
    host.hidden=true;
    return;
  }
  var coverageFact=(packet.facts||[]).filter(function(f){return f.fact_id&&f.fact_id.indexOf('coverage')>-1})[0];
  var cv=coverageFact&&coverageFact.value?coverageFact.value:{};
  var qualified=typeof cv.qualified_days==='number'?cv.qualified_days:null;
  var observed=typeof cv.observed_days==='number'?cv.observed_days:null;
  var rangeText=formatAgentDateRange(packet.range||summary);
  var peakText=(summary.peak_hours||[]).slice(0,2).map(function(p){return String(p.hour).padStart(2,'0')+':00'}).join(' / ');
  if(!peakText)peakText='—';
  var topApps=(summary.top_apps||[]).filter(function(a){return hiddenApps().indexOf(a.name)<0}).slice(0,3).map(function(a){return esc(friendlyName(a.name))}).join(' / ');
  if(!topApps)topApps='—';
  var inputAvg=summary.days?Math.round(summary.input_events/summary.days):0;
  var fgAvg=summary.days?Math.round((summary.foreground_seconds/60)/summary.days):0;
  var validText=qualified!==null&&observed!==null?esc(fmtNum(qualified)+'/'+fmtNum(observed)):'—';
  host.innerHTML=
    '<div class="agent-week-head"><b>'+esc(t('agentWeekTitle'))+'</b><span>'+esc(rangeText)+'</span></div>'+
    '<div class="agent-week-grid">'+
      '<div class="agent-week-cell"><b>'+esc(fmtNum(inputAvg))+'</b><span>'+esc(t('agentWeekDailyInput'))+'</span></div>'+
      '<div class="agent-week-cell"><b>'+esc(fmtNum(fgAvg))+'</b><span>'+esc(t('agentWeekDailyForeground'))+'</span></div>'+
      '<div class="agent-week-cell"><b>'+validText+'</b><span>'+esc(t('agentWeekValidDays'))+'</span></div>'+
    '</div>'+
    '<div class="agent-week-foot">'+esc(t('agentWeekPeak'))+' '+esc(peakText)+' · '+esc(t('agentWeekTopApps'))+' '+topApps+'</div>';
  host.hidden=false;
}

function agentTheoryLensText(card){
  var lens=card&&AGENT_THEORY_LENS[card.theory_id];
  return lens?(lens[getLang()==='en'?'en':'zh']||lens.zh):'';
}

function renderAgentTheoryContext(items){
  var lines=(items||[]).map(agentTheoryLensText).filter(Boolean).filter(function(line,index,list){return list.indexOf(line)===index;}).slice(0,2);
  if(!lines.length)return '';
  return '<div class="agent-detail-section"><h3>'+esc(t('agentObservationTheory'))+'</h3><div class="agent-theory-list">'+lines.map(function(line){
    return '<div class="agent-theory-item"><span>'+esc(line)+'</span></div>';
  }).join('')+'</div></div>';
}

function agentBoundaryText(observation){
  var lang=getLang()==='en'?'en':'zh';
  if(observation&&observation.kind==='coverage'){
    return lang==='en'?'Coverage describes record stability, not a complete account of the day.':'记录覆盖只说明采集是否稳定，不代表全天完整记录。';
  }
  if(observation&&observation.kind==='daily_variation'){
    return lang==='en'?'The gap describes highs and lows within this week only; it does not explain why or rank either day.':'极差只描述本周内部的高低起伏，不说明原因，也不代表哪一天更有价值。';
  }
  if(observation&&observation.kind==='weekend_pattern'){
    return lang==='en'?'The weekend–weekday gap only describes aggregate records; it does not judge either part of the week.':'周末与工作日的差异只描述聚合记录，不代表哪一段安排更好。';
  }
  return lang==='en'?'This compares aggregate records with your own recent range; it cannot explain why the change happened.':'这里只比较聚合记录和你自己的近期范围，不能解释变化为什么发生。';
}

function renderAgentObservationItem(observation,index,feed){
  var correctionNote=observation.corrected
    ? '<div class="agent-correction-note">'+esc(t('agentCorrectedTag'))+'</div>'
    : '';
  var kindLabel=observation&&observation.kind==='coverage'?t('agentObservationCoverage'):t('agentObservationReference');
  return '<button type="button" class="agent-observation" onclick="openAgentObservationDetail('+index+')">'+
    '<div class="agent-observation-head"><span class="agent-kind">'+esc(kindLabel)+'</span><div class="agent-observation-title">'+esc(observation.conclusion||'')+'</div></div>'+
    '<div class="agent-observation-meta">'+esc(agentObservationMetaText(observation,feed))+'</div>'+
    (observation.prompt?'<div class="agent-observation-prompt">'+esc(observation.prompt)+'</div>':'')+
    correctionNote+
  '</button>';
}

async function saveAgentCorrection(observation,kind,payload){
  var body=Object.assign({
    observation_id:observation.observation_id,
    end_date:selectedDate,
    purpose_id:agentPurposeId,
    kind:kind,
    original_text:observation.conclusion||'',
    evidence_hash:agentObservationFeed&&agentObservationFeed.evidence_pack?agentObservationFeed.evidence_pack.evidence_hash:''
  },payload||{});
  try{
    var r=await fetch('/api/agent/corrections',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)
    });
    var d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||t('agentCorrectionError'));
    await loadAgentObservations();
    openAgentObservations();
  }catch(e){
    showModal(t('agentCorrection'),'<div class="modal-error">'+esc(e.message||t('agentCorrectionError'))+'</div>','agent-modal',{returnTo:'agent'});
  }
}

function editAgentObservation(index,kind){
  var feed=agentObservationFeed;
  var observation=feed&&Array.isArray(feed.observations)?feed.observations[index]:null;
  if(!observation)return;
  if(kind==='correction'){
    var text=window.prompt(t('agentCorrectionPrompt'),observation.conclusion||'');
    if(text===null)return;
    saveAgentCorrection(observation,'correction',{corrected_text:text});
  }else if(kind==='rename'){
    var label=window.prompt(t('agentRenamePrompt'),observation.user_label||'');
    if(label===null)return;
    saveAgentCorrection(observation,'rename',{label:label});
  }else if(kind==='ignore'){
    if(!window.confirm(t('agentIgnoreConfirm')))return;
    saveAgentCorrection(observation,'ignore',{});
  }else if(kind==='retract'){
    if(!window.confirm(t('agentRetractConfirm')))return;
    saveAgentCorrection(observation,'retract',{});
  }
}

function agentFeedFingerprint(feed){
  if(!feed||!feed.ok)return '!';
  var obs=(Array.isArray(feed.observations)?feed.observations:[]).map(function(o){
    return [o&&o.kind||'',o&&o.conclusion||'',o&&o.prompt||'',o&&o.user_label||''].join('|');
  }).join('\u0001');
  var hash=feed.evidence_pack&&feed.evidence_pack.evidence_hash||'';
  return hash+'#'+obs;
}
var agentFeedFingerprintCache='';
function renderAgentObservations(feed){
  agentObservationFeed=feed||null;
  var host=document.getElementById('agent-panel');
  var fp=agentFeedFingerprint(feed);
  if(host&&fp&&fp===agentFeedFingerprintCache&&host.getAttribute('data-rendered')==='1'){
    renderAgentSummary(feed);
    renderAgentWeekCard(feed);
    return;
  }
  agentFeedFingerprintCache=fp;
  if(host)host.setAttribute('data-rendered',feed&&feed.ok?'1':'0');
  renderAgentSummary(feed);
  renderAgentWeekCard(feed);
  if(!host)return;
  if(!feed){
    host.innerHTML='<div class="agent-empty">'+esc(t('agentObservationLoading'))+'</div>';
    syncAgentChrome();
    return;
  }
  if(feed.ok===false){
    host.innerHTML='<div class="agent-empty">'+esc(feed.error||t('agentObservationEmpty'))+'</div>';
    syncAgentChrome();
    return;
  }
  var observations=(Array.isArray(feed.observations)?feed.observations:[]).filter(function(observation){return observation&&observation.kind;}).slice(0,6);
  var readiness=agentReferenceReadinessText(feed);
  if(!observations.length){
    host.innerHTML=(readiness?'<div class="agent-readiness">'+esc(readiness)+'</div>':'')+'<div class="agent-empty">'+esc(t('agentObservationEmpty'))+'</div>';
    syncAgentChrome();
    return;
  }
  host.innerHTML=(readiness?'<div class="agent-readiness">'+esc(readiness)+'</div>':'')+observations.map(function(observation){
    return renderAgentObservationItem(observation,feed.observations.indexOf(observation),feed);
  }).join('');
  syncAgentChrome();
}

function renderAgentObservationDetail(observation,feed){
  var range=(feed&&feed.evidence_pack&&feed.evidence_pack.range)||{};
  var validDays=Number(observation.n_valid_days||0);
  var theory=Array.isArray(observation.theory_context)?observation.theory_context:[];
  return '<div class="agent-panel-detail">'+
    '<div class="agent-detail-top">'+
      '<div class="agent-observation-head"><span class="agent-kind">'+esc(observation&&observation.kind==='coverage'?t('agentObservationCoverage'):t('agentObservationReference'))+'</span><div class="agent-detail-title">'+esc(observation.conclusion||'')+'</div></div>'+
      '<div class="agent-detail-sub">'+esc(formatAgentDateRange(range))+' · '+esc(t('agentSummaryValidDays'))+' '+esc(fmtNum(validDays))+'</div>'+
    '</div>'+
    (observation.prompt?'<div class="agent-detail-section"><h3>'+esc(t('agentObservationPrompt'))+'</h3><div class="agent-observation-prompt">'+esc(observation.prompt)+'</div></div>':'')+
    renderAgentTheoryContext(theory)+
    '<div class="agent-detail-section"><h3>'+esc(t('agentObservationBoundary'))+'</h3><div class="agent-detail-text">'+esc(agentBoundaryText(observation))+'</div></div>'+
    '<div class="agent-observation-actions">'+
      '<button type="button" class="agent-correction-btn" onclick="editAgentObservation('+Number((feed.observations||[]).indexOf(observation))+',&quot;correction&quot;)">'+esc(t('agentCorrection'))+'</button>'+
      '<button type="button" class="agent-correction-btn" onclick="editAgentObservation('+Number((feed.observations||[]).indexOf(observation))+',&quot;ignore&quot;)">'+esc(t('agentIgnore'))+'</button>'+
    '</div>'+
  '</div>';
}

function openAgentObservationDetail(index){
  var feed=agentObservationFeed;
  var observation=feed&&Array.isArray(feed.observations)?feed.observations[index]:null;
  if(!observation)return;
  showModal(t('agentObservationDetail'),renderAgentObservationDetail(observation,feed),'agent-modal',{returnTo:'agent'});
}

async function loadAgentPurposes(){
  var changed=false;
  try{
    var r=await fetch('/api/agent/purposes');
    var d=await r.json();
    if(r.ok&&d&&Array.isArray(d.purposes)&&d.purposes.length){
      agentPurposeCatalog=d.purposes.map(function(item){
        return {
          purpose_id:item.purpose_id,
          title:item.title,
          description:item.description,
          supported_metric_ids:item.supported_metric_ids||[],
          allowed_observation_kinds:item.allowed_observation_kinds||[]
        };
      });
    }else{
      agentPurposeCatalog=defaultAgentPurposes();
    }
  }catch(e){
    agentPurposeCatalog=defaultAgentPurposes();
  }
  var exists=false;
  for(var i=0;i<agentPurposeCatalog.length;i++){
    if(agentPurposeCatalog[i]&&agentPurposeCatalog[i].purpose_id===agentPurposeId){
      exists=true;
      break;
    }
  }
  if(!exists){
    var nextPurpose=(agentPurposeCatalog[0]&&agentPurposeCatalog[0].purpose_id)||'understand_personal_rhythm';
    changed=agentPurposeId!==nextPurpose;
    agentPurposeId=nextPurpose;
    localStorage.setItem('kx-agent-purpose',agentPurposeId);
  }
  renderAgentPurposeSwitch();
  syncAgentChrome();
  if(changed)loadAgentObservations();
}

function setAgentView(view){
  agentView=view==='apps'?'apps':'observations';
  localStorage.setItem('kx-agent-view',agentView);
  syncAgentChrome();
  if(agentView!=='apps'&&(!agentObservationFeed||agentObservationFeed.ok===false)){
    loadAgentObservations();
  }
}

function setAgentPurpose(purposeId){
  if(!purposeId)return;
  var list=agentPurposeCatalog&&agentPurposeCatalog.length?agentPurposeCatalog:defaultAgentPurposes();
  var exists=false;
  for(var i=0;i<list.length;i++){
    if(list[i]&&list[i].purpose_id===purposeId){
      exists=true;
      break;
    }
  }
  if(!exists)return;
  if(agentPurposeId===purposeId){
    syncAgentChrome();
    return;
  }
  agentPurposeId=purposeId;
  localStorage.setItem('kx-agent-purpose',purposeId);
  renderAgentPurposeSwitch();
  loadAgentObservations();
}

function openAgentObservations(){
  agentPurposeId='understand_personal_rhythm';
  showModal(t('agentViewObservations'),
    '<div class="agent-shell">'+
      '<div class="agent-summary" id="agent-summary"></div>'+
      '<div class="agent-week-card" id="agent-week-card" hidden></div>'+
      '<div class="agent-trend-card" id="agent-trend-card" hidden></div>'+
      '<div class="agent-return-card" id="agent-return-card" hidden></div>'+
      '<div class="agent-panel" id="agent-panel"><div class="agent-empty">'+esc(t('agentObservationLoading'))+'</div></div>'+
      '<button type="button" class="agent-deep-btn" onclick="openAgentDeepRead()">'+esc(t('agentDeepRead'))+'</button>'+
    '</div>',
    'agent-modal',
    {returnTo:null}
  );
  if(agentObservationFeed&&agentObservationFeed.ok){
    renderAgentObservations(agentObservationFeed);
  }else{
    loadAgentObservations();
  }
  refreshAppLabels();
  loadTrendCard();
  loadReturnCard();
}

async function refreshAppLabels(){
  try{
    var r=await fetch('/api/app-labels');
    var d=await r.json();
    if(d&&d.ok)APP_LABELS=d.labels||{};
  }catch(e){}
}

async function loadTrendCard(){
  var host=document.getElementById('agent-trend-card');
  if(!host)return;
  try{
    var r=await fetch('/api/trend?weeks=12');
    var d=await r.json();
    if(!r.ok||!d||!d.weeks)throw new Error('trend failed');
    renderTrendCard(d);
  }catch(e){
    host.hidden=true;
  }
}

function renderTrendCard(trend){
  var host=document.getElementById('agent-trend-card');
  if(!host)return;
  var weeks=(trend&&Array.isArray(trend.weeks))?trend.weeks:[];
  var baseline=(trend&&trend.baseline&&trend.baseline.input)||{};
  if(!weeks.length){
    host.hidden=true;
    return;
  }
  var en=getLang()==='en';
  var max=Math.max.apply(null,weeks.map(function(w){return w.avg_daily_input||0}).concat([1]));
  var latest=weeks[weeks.length-1];
  var latestAvg=latest.avg_daily_input||0;
  var baselineReady=baseline.median!==null&&baseline.median!==undefined&&baseline.p25!==null&&baseline.p75!==null;
  var footText;
  if(baselineReady){
    var position=latestAvg<baseline.p25?'below':(latestAvg>baseline.p75?'above':'within');
    var positionText={
      below:en?'below your typical range':'低于你的典型范围',
      above:en?'above your typical range':'高于你的典型范围',
      within:en?'within your typical range':'在你的典型范围内'
    }[position];
    footText=t('trendWeekAvg')+' '+fmtNum(Math.round(latestAvg))+' · '+positionText+'（'+fmtNum(Math.round(baseline.p25))+'–'+fmtNum(Math.round(baseline.p75))+'）';
  }else{
    footText=t('trendWeekAvg')+' '+fmtNum(Math.round(latestAvg))+' · '+t('agentBaselineFactsOnly').replace(/^[，,]\s*/,'')+'（'+fmtNum(baseline.n_valid_days||0)+'）';
  }
  var bars=weeks.map(function(w){
    var h=Math.max(2,Math.round((w.avg_daily_input||0)/Math.max(1,max)*54));
    var band='';
    if(baselineReady){
      var p25h=Math.max(2,Math.round((baseline.p25||0)/Math.max(1,max)*54));
      var p75h=Math.max(2,Math.round((baseline.p75||0)/Math.max(1,max)*54));
      var bandBottom=Math.min(p25h,p75h);
      var bandHeight=Math.max(3,Math.abs(p75h-p25h));
      band='<div class="trend-bar-band" style="height:'+bandHeight+'px;bottom:'+bandBottom+'px;left:6px;right:6px"></div>';
    }
    return '<div class="trend-bar-col" title="'+esc(w.week_start+' · '+fmtNum(Math.round(w.avg_daily_input)))+'">'+band+'<div class="trend-bar" style="height:'+h+'px"></div><span>'+esc(String(w.week_start).slice(5).replace('-','/'))+'</span></div>';
  }).join('');
  host.innerHTML=
    '<div class="agent-week-head"><b>'+esc(t('trendTitle'))+'</b><span>'+esc(en?'12 weeks':'最近 12 周')+'</span></div>'+
    '<div class="trend-bars" style="grid-template-columns:repeat('+weeks.length+',minmax(0,1fr))">'+bars+'</div>'+
    '<div class="agent-week-foot">'+esc(footText)+'</div>';
  host.hidden=false;
}

async function loadReturnCard(){
  var host=document.getElementById('agent-return-card');
  if(!host)return;
  try{
    var r=await fetch('/api/return-intervals?days=7');
    var d=await r.json();
    renderReturnCard(d);
  }catch(e){
    host.hidden=true;
  }
}

function renderReturnCard(ri){
  var host=document.getElementById('agent-return-card');
  if(!host)return;
  if(!ri||!ri.active_days||!ri.switch_count){
    host.hidden=true;
    return;
  }
  var en=getLang()==='en';
  var apps=(ri.apps||[]).slice(0,3).map(function(a){
    return '<div class="agent-return-app"><b>'+esc(friendlyName(a.name))+'</b><span>'+esc(fmtNum(a.returns))+(en?'×':' 次')+' · '+esc(fmtTime(a.median_seconds))+'</span></div>';
  }).join('');
  host.innerHTML=
    '<div class="agent-week-head"><b>'+esc(t('returnTitle'))+'</b><span>'+esc(ri.days+(en?'d':'天'))+'</span></div>'+
    '<div class="agent-week-grid">'+
      '<div class="agent-week-cell"><b>'+esc(fmtNum(ri.switches_per_day))+'</b><span>'+esc(t('returnPerDay'))+'</span></div>'+
      '<div class="agent-week-cell"><b>'+esc(fmtNum(ri.switch_count))+'</b><span>'+esc(t('returnTotal'))+'</span></div>'+
    '</div>'+
    (apps?'<div class="agent-return-apps">'+apps+'</div>':'')+
    '<div class="agent-week-foot">'+esc(t('returnAppsHint'))+'</div>';
  host.hidden=false;
}

function openAgentDeepRead(){
  var feed=agentObservationFeed;
  if(!feed||!feed.ok||!Array.isArray(feed.observations)||!feed.observations.length){
    showModal(t('agentDeepReadTitle'),'<div class="agent-readiness">'+esc(t('agentDeepReadNoObservations'))+'</div>','agent-modal',{returnTo:'agent'});
    return;
  }
  showModal(t('agentDeepReadTitle'),
    '<div class="agent-shell" id="agent-deep-read-shell">'+
      '<div class="agent-deep-actions"><button class="settings-action" onclick="runAgentDeepRead()"><b>'+esc(t('agentDeepReadRun'))+'</b></button></div>'+
      '<div class="agent-deep-status" id="agent-deep-status"></div>'+
    '</div>',
    'agent-modal',
    {returnTo:'agent'}
  );
}

function agentDeepReadLocalItems(feed){
  if(!feed||!feed.ok||!Array.isArray(feed.observations))return '';
  return feed.observations.map(function(observation){
    return '<div class="agent-observation">'+
      '<div class="agent-observation-head"><span class="agent-kind">'+esc(observation.kind==='coverage'?t('agentObservationCoverage'):t('agentObservationReference'))+'</span><div class="agent-observation-title">'+esc(observation.conclusion||'')+'</div></div>'+
      '<div class="agent-observation-meta">'+esc(agentObservationMetaText(observation,feed))+'</div>'+
      '<div class="agent-observation-boundary">'+esc(observation.boundary||'')+'</div>'+
    '</div>';
  }).join('');
}

async function runAgentDeepRead(){
  if(!await ensureAiKeyReady()){showAiSetupGuide('agent');return}
  var status=document.getElementById('agent-deep-status');
  if(status)status.innerHTML='<div class="agent-readiness">'+esc(t('agentDeepReadQueued'))+'</div>';
  try{
    var r=await fetch('/api/agent/jobs',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        end_date:selectedDate,
        scope:'week',
        purpose:agentPurposeId,
        thinking_enabled:!!(SETTINGS_STATE.ai_thinking_enabled)
      })
    });
    var d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||t('agentDeepReadSubmitFailed'));
    await pollAgentDeepRead(d.job_id);
  }catch(e){
    renderAgentDeepReadFallback(e.message||t('agentDeepReadSubmitFailed'));
  }
}

async function pollAgentDeepRead(jobId){
  var started=Date.now();
  var status=document.getElementById('agent-deep-status');
  while(true){
    try{
      var r=await fetch('/api/agent/jobs?id='+encodeURIComponent(jobId));
      var d=await r.json();
      if(!r.ok||!d.ok)throw new Error(d.error||'job error');
    }catch(e){
      renderAgentDeepReadFallback(e.message||t('agentDeepReadFailed'));
      return;
    }
    if(d.status==='completed'){
      renderAgentDeepReadResult(d.result);
      return;
    }
    if(d.status==='failed'){
      renderAgentDeepReadFallback(d.error||t('agentDeepReadFailed'));
      return;
    }
    if(Date.now()-started>90000){
      renderAgentDeepReadFallback(t('agentDeepReadFailed'));
      return;
    }
    if(status)status.innerHTML='<div class="agent-readiness">'+esc(t('agentDeepReadRunning'))+'</div>';
    await new Promise(function(resolve){setTimeout(resolve,800)});
  }
}

function renderAgentDeepReadResult(result){
  var host=document.getElementById('agent-deep-read-shell');
  if(!host)return;
  if(!result||!result.model_used||result.fallback){
    renderAgentDeepReadFallback(result&&result.fallback_reason?result.fallback_reason:'');
    return;
  }
  var findings=(result.findings||[]).map(function(item){
    return '<div class="insight-item clue"><span class="insight-badge"></span><span>'+esc(item||'')+'</span></div>';
  }).join('');
  host.innerHTML=renderAiResponseBody(result,findings);
}

function renderAgentDeepReadFallback(reason){
  var host=document.getElementById('agent-deep-read-shell');
  if(!host)return;
  host.innerHTML='<div class="agent-readiness">'+esc(t('agentDeepReadFallback'))+'</div>';
}

async function loadAgentObservations(opts){
  var silent=opts&&opts.silent;
  var panel=document.getElementById('agent-panel');
  if(agentView!=='apps'&&!silent){
    agentObservationFeed=null;
    syncAgentChrome();
  }
  if(panel&&agentView!=='apps'&&!silent){
    panel.innerHTML='<div class="agent-empty">'+esc(t('agentObservationLoading'))+'</div>';
    panel.setAttribute('data-rendered','0');
  }
  try{
    var r=await fetch('/api/agent/observations?date='+encodeURIComponent(selectedDate)+'&purpose='+encodeURIComponent(agentPurposeId));
    var d=await r.json();
    if(!r.ok||d.ok===false)throw new Error(d.error||t('agentObservationLoading'));
    renderAgentObservations(d);
  }catch(e){
    if(!silent&&agentView!=='apps'){
      renderAgentObservations({ok:false,error:e.message||t('agentObservationLoading')});
    }else if(!silent){
      syncAgentChrome();
    }
  }
}

async function windowControl(action){
  try{
    if(!window.pywebview||!window.pywebview.api)return;
    if(action==='minimize')await window.pywebview.api.minimize();
    if(action==='close')await window.pywebview.api.close();
  }catch(e){}
}

/* ── Theme ─────────────────────── */
function getTheme(){return localStorage.getItem('kx-theme')||'light'}
function getAccent(){return localStorage.getItem('kx-accent')||'green'}
function syncNativeTheme(t){
  try{
    if(window.pywebview&&window.pywebview.api&&window.pywebview.api.set_native_theme){
      window.pywebview.api.set_native_theme(t);
    }
  }catch(e){}
}
function applyTheme(t){
  document.documentElement.setAttribute('data-theme',t);
  document.documentElement.setAttribute('data-accent',getAccent());
  var themeToggle=document.getElementById('theme-toggle');
  if(themeToggle)themeToggle.textContent=t==='dark'?'☀️':'🌙';
  document.querySelectorAll('[data-theme-choice]').forEach(function(btn){
    btn.classList.toggle('active',btn.dataset.themeChoice===t);
  });
  document.querySelectorAll('[data-accent-choice]').forEach(function(btn){
    btn.classList.toggle('active',btn.dataset.accentChoice===getAccent());
  });
  syncNativeTheme(t);

  // 更新 meta theme-color
  const metaTheme = document.querySelector('meta[name="theme-color"]');
  if (metaTheme) {
    metaTheme.content = t === 'dark' ? '#151515' : '#f7f7f7';
  }
}
function setThemeChoice(t){localStorage.setItem('kx-theme',t);applyTheme(t);renderSettings(SETTINGS_STATE);savePreferencePatch({theme:t})}
function setAccentChoice(t){t=t||'green';localStorage.setItem('kx-accent',t);applyTheme(getTheme());renderSettings(SETTINGS_STATE);savePreferencePatch({accent:t});renderHeatmap();}
function toggleTheme(){var n=getTheme()==='dark'?'light':'dark';setThemeChoice(n)}
applyTheme(getTheme());
applyLanguage();
window.addEventListener('pywebviewready',function(){syncNativeTheme(getTheme())});

/* ── Utils ─────────────────────── */
function fmtTime(s){s=Math.max(0,Math.round(s||0));var h=Math.floor(s/3600),m=Math.floor((s%3600)/60);if(h>0)return h+'h'+String(m).padStart(2,'0')+'m';if(m>0)return m+'m';return s+'s'}
function fmtNum(n){return n.toLocaleString(getLang()==='en'?'en-US':'zh-CN')}
function fmtDateShort(s){var p=String(s||'').split('-');return p.length===3?(Number(p[1])+'/'+Number(p[2])):s}
function fmtDateLabel(s){var p=String(s||'').split('-');if(p.length!==3)return s;return getLang()==='en'?(Number(p[1])+'/'+Number(p[2])):(Number(p[1])+'月'+Number(p[2])+'日')}
function fmtHour(h){return String(h).padStart(2,'0')+':00'}
function fmtStamp(s){if(!s)return '—';var d=new Date(s);return (d.getMonth()+1)+'/'+d.getDate()+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')}
function fmtHourRange(h){return String(h).padStart(2,'0')+':00-'+String((h+1)%24).padStart(2,'0')+':00'}

var FILLS=['f0','f1','f2','f3','f4','f5','f6','f7','f8','f9'];
var RANKS=['r0','r1','r2','r3','rx','rx','rx','rx','rx','rx'];
var APP_MAP={'Code':'VS Code','Code - Insiders':'VS Code','chrome':'Chrome','msedge':'Edge','firefox':'Firefox','opera':'Opera','brave':'Brave','explorer':'文件管理器','explorer.exe':'文件管理器','cmd':'命令提示符','powershell':'PowerShell','WindowsTerminal':'终端','wt':'终端','pwsh':'PowerShell','bash':'Bash','wsl':'WSL','devenv':'VS','idea64':'IntelliJ','idea':'IntelliJ','webstorm64':'WebStorm','pycharm64':'PyCharm','clion64':'CLion','notepad++':'Notepad++','notepad':'记事本','Sublime Text':'Sublime','atom':'Atom','cursor':'Cursor','Cursor':'Cursor','Trae':'Trae','Windsurf':'Windsurf','Codex':'Codex','codex':'Codex','slack':'Slack','Teams':'Teams','wechat':'微信','WeChat':'微信','QQ':'QQ','TIM':'TIM','dingtalk':'钉钉','Dingtalk':'钉钉','DingTalk':'钉钉','feishu':'飞书','Lark':'飞书','telegram':'Telegram','Discord':'Discord','whatsapp':'WhatsApp','zoom':'Zoom','ZoomIt':'ZoomIt','Spotify':'Spotify','网易云音乐':'网易云','CloudMusic':'网易云','QQMusic':'QQ音乐','kugou':'酷狗','KuGou':'酷狗','PotPlayer':'PotPlayer','vlc':'VLC','mpv':'mpv','Photoshop':'Photoshop','Illustrator':'Illustrator','Figma':'Figma','figma':'Figma','Adobe Premiere Pro':'Premiere','AfterFX':'AE','Sai':'SAI','SAI2':'SAI2','ClipStudioPaint':'CSP','steam':'Steam','EpicGamesLauncher':'Epic','epic':'Epic','obsidian':'Obsidian','Notion':'Notion','Typora':'Typora','Mark Text':'Mark Text','logseq':'Logseq','Word':'Word','WINWORD':'Word','Excel':'Excel','EXCEL':'Excel','PowerPoint':'PPT','POWERPNT':'PPT','OUTLOOK':'Outlook','OneNote':'OneNote','ONENOTE':'OneNote','Postman':'Postman','Docker Desktop':'Docker','docker':'Docker','Git':'Git','GitHub Desktop':'GitHub','node':'Node','python':'Python','python3':'Python','pythonw':'Python','pythonw.exe':'Python','OBS':'OBS','obs64':'OBS','ShareX':'ShareX','Snipaste':'Snipaste','AutoHotkey':'AHK','Quicker':'Quicker','Flow.Launcher':'Flow','Everything':'Everything','vmware':'VMware','TypeTracker':'扣舷-数字节律','TypeTracker.exe':'扣舷-数字节律','InteractionRhythm':'扣舷-数字节律','InteractionRhythm.exe':'扣舷-数字节律','交互节律':'扣舷-数字节律','扣舷-数字节律':'扣舷-数字节律','叩舷':'扣舷-数字节律','扣舷':'扣舷-数字节律','Claude':'Claude','ChatGPT':'ChatGPT','Perplexity':'Perplexity','DeepL':'DeepL','有道词典':'有道','WPS':'WPS','wps':'WPS','et':'WPS表格','wpp':'WPS演示','Foxmail':'Foxmail','qBittorrent':'qBit','studio64':'Android','RustRover':'RustRover','fleet':'Fleet','Unity':'Unity','UnrealEditor':'UE','Godot':'Godot','quark':'夸克','quark.exe':'夸克','RevoUnin':'Revo卸载','RevoUnin.exe':'Revo卸载','GeekDesk':'GeekDesk','GeekDesk.exe':'GeekDesk','CLaunch':'CLaunch','CLaunch.exe':'CLaunch','OpenCode':'OpenCode'};
var APP_MAP_EN={'explorer':'File Explorer','explorer.exe':'File Explorer','cmd':'Command Prompt','powershell':'PowerShell','WindowsTerminal':'Terminal','wechat':'WeChat','WeChat':'WeChat','dingtalk':'DingTalk','feishu':'Feishu','Lark':'Feishu','网易云音乐':'NetEase Music','CloudMusic':'NetEase Music','QQMusic':'QQ Music','PotPlayer':'PotPlayer','notepad':'Notepad','TypeTracker':'Interaction Rhythm','TypeTracker.exe':'Interaction Rhythm','InteractionRhythm':'Interaction Rhythm','InteractionRhythm.exe':'Interaction Rhythm','交互节律':'Interaction Rhythm','扣舷-数字节律':'Interaction Rhythm','叩舷':'Interaction Rhythm','扣舷':'Interaction Rhythm','有道词典':'Youdao','文件管理器':'File Explorer'};
function friendlyName(a){
  if(APP_LABELS[a])return APP_LABELS[a];
  if(getLang()==='en'){
    if(APP_MAP_EN[a])return APP_MAP_EN[a];
    var be=String(a||'').replace(/\.exe$/i,'');
    if(APP_MAP_EN[be])return APP_MAP_EN[be];
  }
  if(APP_MAP[a])return APP_MAP[a];
  var b=String(a||'').replace(/\.exe$/i,'');
  if(APP_MAP[b])return APP_MAP[b];
  if(['interactionrhythm','typetracker'].indexOf(b.toLowerCase())>=0)return getLang()==='en'?'Interaction Rhythm':'扣舷-数字节律';
  return b;
}
function hiddenApps(){return Array.isArray(SETTINGS_STATE.hidden_apps)?SETTINGS_STATE.hidden_apps:[]}
function appVisible(a){return hiddenApps().indexOf(String((a&&a.app)||''))<0}
function appDisplayName(a){return (a&&a.label)?a.label:friendlyName((a&&a.app)||a)}
async function editAppLabel(app,currentLabel){
  var en=getLang()==='en';
  var label=window.prompt(en?'Custom name for this app (leave empty to clear):':'给这个应用起一个自定义名称（留空清除）：',currentLabel||'');
  if(label===null)return;
  try{
    var r=await fetch('/api/app-labels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({app:app,label:label})});
    var d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||(en?'Save failed':'保存失败'));
    APP_LABELS[app]=(label||'').trim()||'';
    if(!APP_LABELS[app])delete APP_LABELS[app];
    loadApps();
  }catch(e){
    showModal(t('appLabelEdit'),'<div class="modal-error">'+esc(e.message||t('appLabelError'))+'</div>');
  }
}
function appSubName(a){var n=friendlyName(a);return n==='扣舷-数字节律'||n==='交互节律'||n==='Interaction Rhythm'?n:a}
function appIcon(a){
  var b=String(a||'').replace(/\.exe$/i,'');
  var lower=b.toLowerCase();
  if(['interactionrhythm','typetracker','叩舷','扣舷','交互节律','扣舷-数字节律'].indexOf(lower)>=0)return getLang()==='en'?'I':'律';
  var name=friendlyName(a)||b||'?';
  return String(name).trim().slice(0,1).toUpperCase();
}
function esc(s){return String(s||'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function tipAttr(s){return esc(s).replace(/\n/g,'&#10;')}
function iconHtml(a){
  var fallback=esc(appIcon(a.app));
  if(a.path){
    return '<span class="app-icon" data-fallback="'+fallback+'"><img src="/api/icon?path='+encodeURIComponent(a.path)+'" alt="" loading="eager" decoding="async" onerror="this.parentNode.textContent=this.parentNode.dataset.fallback"></span>';
  }
  return '<span class="app-icon">'+fallback+'</span>';
}
function detailIconHtml(d){
  var fallback=esc(appIcon(d.app));
  if(d.path){
    return '<div class="detail-icon" data-fallback="'+fallback+'"><img src="/api/icon?path='+encodeURIComponent(d.path)+'" alt="" onerror="this.parentNode.textContent=this.parentNode.dataset.fallback"></div>';
  }
  return '<div class="detail-icon">'+fallback+'</div>';
}

/* ── Modal ─────────────────────── */
var modalReturnTo=null;
var modalCloseTimer=null;
function showModal(title,html,variant,options){
  options=options||{};
  if(Object.prototype.hasOwnProperty.call(options,'returnTo'))modalReturnTo=options.returnTo;
  document.getElementById('modal-title').textContent=title;
  var headExtra=document.getElementById('modal-head-extra');
  if(headExtra){
    if(options.headerHtml){headExtra.innerHTML=options.headerHtml;headExtra.style.display='';}
    else{headExtra.innerHTML='';headExtra.style.display='none';}
  }
  var body=document.getElementById('modal-body');
  body.innerHTML=html;
  body.scrollTop=0;
  var card=document.getElementById('modal-card');
  if(card)card.className='modal-card'+(variant?' '+variant:'');
  var modal=document.getElementById('modal');
  if(modalCloseTimer){clearTimeout(modalCloseTimer);modalCloseTimer=null}
  modal.classList.remove('closing');
  modal.hidden=false;
  document.body.style.overflow = 'hidden'; // 防止背景滚动
}
function hideModal(done){
  var modal=document.getElementById('modal');
  if(modal.hidden){
    document.body.style.overflow = '';
    if(done)done();
    return;
  }
  modal.classList.add('closing');
  if(modalCloseTimer)clearTimeout(modalCloseTimer);
  modalCloseTimer=setTimeout(function(){
    modal.hidden=true;
    modal.classList.remove('closing');
    document.body.style.overflow = '';
    modalCloseTimer=null;
    if(done)done();
  },150);
}
function closeModal(){
  var card=document.getElementById('modal-card');
  var variant=card?(card.className||''):'';
  if(variant.indexOf('analysis-modal')!==-1||variant.indexOf('insight-modal')!==-1)insightLive=false;
  var next=modalReturnTo;
  modalReturnTo=null;
  if(next==='settings'){
    hideModal(openSettings);
    return;
  }
  if(next==='agent'){
    hideModal(openAgentObservations);
    return;
  }
  hideModal();
}
document.getElementById('modal').addEventListener('click',function(e){if(e.target.id==='modal')closeModal()});
window.addEventListener('keydown',function(e){if(e.key==='Escape')closeModal()});

// 移动端：点击 modal 外部关闭
document.getElementById('modal')?.addEventListener('touchend', function(e) {
  if (e.target.id === 'modal') {
    closeModal();
  }
});

function renderHourBars(values){
  var mx=Math.max(1,...values);
  return '<div class="bar-chart">'+values.map(function(v,h){
    var height=v>0?Math.max(6,(v/mx)*100):2;
    return '<div class="bar" title="'+fmtHour(h)+' · '+fmtTime(v)+'" style="height:'+height.toFixed(1)+'%"></div>';
  }).join('')+'</div>';
}
function renderDayBars(days){
  var mx=Math.max(1,...days.map(function(d){return d.seconds||d.responses||0}));
  return '<div class="day-bars">'+days.map(function(d){
    var v=d.seconds||d.responses||0,height=v>0?Math.max(6,(v/mx)*100):2;
    return '<div class="day-bar-wrap" title="'+esc(d.date)+' · '+(d.seconds!=null?fmtTime(v):fmtNum(v)+' '+(getLang()==='en'?'responses':'次'))+'"><div class="day-bar" style="height:'+height.toFixed(1)+'%"></div><span>'+fmtDateShort(d.date)+'</span></div>';
  }).join('')+'</div>';
}
function renderDetailHeat(days){
  var mx=1;
  days.forEach(function(day){(day.hourly||[]).forEach(function(v){if(v>mx)mx=v})});
  return '<div class="detail-heat">'+days.map(function(day){
    return '<div><div class="detail-day-label">'+fmtDateShort(day.date)+'</div><div class="detail-day">'+(day.hourly||[]).map(function(v,h){
      return '<div class="mini-cell" title="'+fmtDateShort(day.date)+' '+fmtHour(h)+' · '+fmtTime(v)+'" style="background:'+heatColor(v,mx)+'"></div>';
    }).join('')+'</div></div>';
  }).join('')+'</div>';
}
async function downloadExport(format){
  var r=await fetch('/api/export?format='+format+'&days=30');
  var blob=await r.blob();
  var cd=r.headers.get('Content-Disposition')||'';
  var m=/filename="([^"]+)"/.exec(cd);
  var name=m?m[1]:'interaction-rhythm-30d.'+format;
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();
  setTimeout(function(){URL.revokeObjectURL(url)},500);
}
function openExport(){
  showModal(getLang()==='en'?'Export Data':'导出数据',
    '<p class="muted">'+(getLang()==='en'?'Export the last 30 days. JSON keeps the full structure; CSV opens well in spreadsheets.':'导出最近 30 天的数据。JSON 适合继续做手感回放或其他分析，CSV 适合放进表格里查看。')+'</p>'+
    '<div class="action-grid">'+
      '<a class="action-card" href="#" onclick="downloadExport(&quot;json&quot;);return false"><b>JSON</b><span>'+(getLang()==='en'?'Full record structure for further analysis.':'保留完整记录结构，适合后续分析。')+'</span></a>'+
      '<a class="action-card" href="#" onclick="downloadExport(&quot;csv&quot;);return false"><b>CSV</b><span>'+(getLang()==='en'?'Spreadsheet-friendly file for sorting and filtering.':'普通表格文件，打开后就能筛选和排序。')+'</span></a>'+
    '</div>'
  );
}
var AI_EXPORT_PROMPT_ZH='你是一名个人数字节律观察助手。我会给你一份由"扣舷-数字节律"导出的最近 30 天本地聚合数据（JSON），只包含时间、输入事件数、鼠标/键盘响应、前台应用与覆盖时长等统计，不包含任何输入内容、窗口标题、坐标或正文。\n\n请按以下要求分析：\n1. 只描述数据中实际存在的规律：主要使用时段、应用构成、输入密度起伏、工作日与周末差异。\n2. 使用概率化措辞，不下绝对结论，不把相关性写成因果。\n3. 不猜测我的工作内容，不做性格、心理或医学诊断，不与其他用户比较排名。\n4. 给出 1-3 条回想问题（帮助我回忆那几天在做什么）和 1-3 条低成本行动建议（如调整时段、减少连续使用）。\n5. 输出：一段总结 + 3-5 条发现 + 回想问题 + 行动建议。\n\n注意：数据是聚合统计，存在采集覆盖缺口，请基于证据说话，不要编造数据。';
var AI_EXPORT_PROMPT_EN='You are a personal digital-rhythm observation assistant. I will give you a JSON export of the last 30 days of local aggregate data from "Interaction Rhythm": time, input events, mouse/keyboard responses, foreground apps, and coverage duration only. No typed content, window titles, coordinates, or text bodies are included.\n\nPlease: 1) Describe only patterns actually present in the data: main usage windows, app mix, input density, weekday vs weekend differences. 2) Use probabilistic wording; do not state absolutes or treat correlation as causation. 3) Do not guess my work content, do not make personality/psychology/medical diagnoses, and do not rank me against other users. 4) Give 1-3 recall questions (to help me remember what I was doing those days) and 1-3 low-cost action suggestions (e.g. adjust timing, reduce continuous use). 5) Output: a summary + 3-5 findings + recall questions + action suggestions.\n\nNote: the data is aggregated and may have coverage gaps; speak only from the evidence and do not fabricate.';
function aiExportPromptText(){return getLang()==='en'?AI_EXPORT_PROMPT_EN:AI_EXPORT_PROMPT_ZH}
function openExportWithPrompt(){
  showModal(t('aiPromptTitle'),
    '<p class="muted">'+esc(t('aiExportGuide'))+'</p>'+
    '<textarea class="settings-input ai-prompt-area" id="ai-export-prompt" readonly>'+esc(aiExportPromptText())+'</textarea>'+
    '<div class="settings-actions">'+
      '<button class="settings-action" onclick="copyAiExportPrompt()"><b>'+esc(t('aiPromptCopy'))+'</b></button>'+
      '<button class="settings-action" onclick="downloadExport(&quot;json&quot;)"><b>'+esc(t('aiPromptJson'))+'</b></button>'+
    '</div>',
    'ai-prompt-modal',
    {returnTo:'settings'}
  );
}
async function copyAiExportPrompt(){
  var box=document.getElementById('ai-export-prompt');
  try{
    await copyText(box?box.value:aiExportPromptText());
    if(box)box.setAttribute('data-copied','1');
    var btn=document.querySelector('#ai-prompt-modal button');
    if(btn){btn.innerHTML='<b>'+esc(t('aiPromptCopied'))+'</b>'}
  }catch(e){
    showModal(t('aiPromptTitle'),'<div class="modal-error">'+esc(t('copyFailed'))+'</div>','ai-prompt-modal',{returnTo:'settings'});
  }
}
async function ensureAiKeyReady(){
  try{
    var r=await fetch('/api/ai/config');
    var d=await r.json();
    return !!(d&&d.ok&&d.key_configured);
  }catch(e){return false}
}
function showAiSetupGuide(returnTo){
  showModal(t('aiNoKeyTitle'),
    '<p class="muted">'+esc(t('aiNoKeyBody'))+'</p>'+
    '<div class="settings-actions">'+
      '<button class="settings-action" onclick="closeModal();openSettings()"><b>'+esc(t('aiSetupNow'))+'</b></button>'+
      '<button class="settings-action" onclick="openExportWithPrompt()"><b>'+esc(t('aiExportPrompt'))+'</b></button>'+
    '</div>',
    'ai-guide-modal',
    {returnTo:returnTo}
  );
}

var AI_GUIDE_SEEN_KEY='kx-ai-guide-v0.10.3';
var aiGuidePending=false;
function markAiGuideSeen(){try{localStorage.setItem(AI_GUIDE_SEEN_KEY,'1')}catch(e){}}
function aiGuideSeen(){try{return localStorage.getItem(AI_GUIDE_SEEN_KEY)==='1'}catch(e){return true}}
var AI_CONFIG_INDEX=[
  {key:'svc',action:'settings',icon:'\u2601'},
  {key:'key',action:'settings',icon:'\uD83D\uDD11'},
  {key:'prompt',action:'settings',icon:'\u270E'},
  {key:'think',action:'settings',icon:'\u2699'},
  {key:'preview',action:'preview',icon:'\u25C9'},
  {key:'exportPrompt',action:'exportPrompt',icon:'\u21F2'},
  {key:'export',action:'export',icon:'\u2913'},
  {key:'fulltext',action:'none',icon:'\u25A4'}
];
function aiIndexTitle(it){var k=it.key.charAt(0).toUpperCase()+it.key.slice(1);return t('aiIdx'+k+'Title')}
function aiIndexDesc(it){var k=it.key.charAt(0).toUpperCase()+it.key.slice(1);return t('aiIdx'+k+'Desc')}
function aiIndexRow(it){
  var btn='';
  if(it.action==='settings')btn='<button class="settings-action" onclick="closeModal();openSettings()"><b>'+esc(t('aiIndexSettings'))+'</b></button>';
  else if(it.action==='preview')btn='<button class="settings-action" onclick="previewAiOutbound()"><b>'+esc(t('aiIndexOpen'))+'</b></button>';
  else if(it.action==='exportPrompt')btn='<button class="settings-action" onclick="openExportWithPrompt()"><b>'+esc(t('aiIndexOpen'))+'</b></button>';
  else if(it.action==='export')btn='<button class="settings-action" onclick="openExport()"><b>'+esc(t('aiIndexOpen'))+'</b></button>';
  return '<div class="ai-index-row"><div class="row-copy"><b>'+esc(it.icon)+' '+esc(aiIndexTitle(it))+'</b><span>'+esc(aiIndexDesc(it))+'</span></div>'+(btn?'<div class="row-btn">'+btn+'</div>':'')+'</div>';
}
function aiConfigIndexHtml(){
  return '<p class="muted">'+esc(t('aiConfigIndexDesc'))+'</p>'+
    '<div class="ai-index-list">'+AI_CONFIG_INDEX.map(aiIndexRow).join('')+'</div>';
}
function showAiConfigIndex(){
  showModal(t('aiConfigIndexTitle'),aiConfigIndexHtml(),'ai-guide-modal',{returnTo:null});
}
function aiConfigGuideHtml(){
  var steps=[
    ['1',t('aiGuideStep1'),t('aiGuideStep1Desc')],
    ['2',t('aiGuideStep2'),t('aiGuideStep2Desc')],
    ['3',t('aiGuideStep3'),t('aiGuideStep3Desc')],
    ['4',t('aiGuideStep4'),t('aiGuideStep4Desc')]
  ];
  var stepHtml=steps.map(function(s){
    return '<div class="ai-guide-step"><div class="step-num">'+esc(s[0])+'</div><div><b>'+esc(s[1])+'</b><span>'+esc(s[2])+'</span></div></div>';
  }).join('');
  return '<p class="muted">'+esc(t('aiGuideSubtitle'))+'</p>'+
    '<div class="ai-guide-steps">'+stepHtml+'</div>'+
    '<div class="ai-guide-index"><button class="ai-guide-index-head" onclick="showAiConfigIndex()"><b>'+esc(t('aiGuideIndex'))+'</b><span>'+esc(t('aiGuideIndexDesc'))+' \u2192</span></button>'+
    '<div class="ai-index-list">'+AI_CONFIG_INDEX.slice(0,4).map(aiIndexRow).join('')+'</div></div>'+
    '<div class="settings-actions">'+
      '<button class="settings-action" onclick="closeModal();openSettings()"><b>'+esc(t('aiGuideGoto'))+'</b></button>'+
      '<button class="settings-action" onclick="openExportWithPrompt()"><b>'+esc(t('aiGuideExport'))+'</b></button>'+
    '</div>';
}
function showAiConfigGuide(){
  markAiGuideSeen();
  showModal(t('aiGuideTitle'),aiConfigGuideHtml(),'ai-guide-modal',{returnTo:null});
}
async function showAiConfigGuideIfFirstRun(){
  aiGuidePending=false;
  if(aiGuideSeen())return;
  var ready=await ensureAiKeyReady();
  if(!ready)showAiConfigGuide();
}
async function copyText(text){
  if(navigator.clipboard&&navigator.clipboard.writeText){
    return navigator.clipboard.writeText(text);
  }
  var ta=document.createElement('textarea');
  ta.value=text;
  ta.setAttribute('readonly','');
  ta.style.position='fixed';
  ta.style.left='-9999px';
  document.body.appendChild(ta);
  ta.select();
  var ok=document.execCommand('copy');
  ta.remove();
  if(!ok)throw new Error('copy failed');
}
async function copyMediaSnapshot(){
  var text=(document.getElementById('media-draft')||{}).value||'';
  var state=document.getElementById('media-copy-state');
  try{
    await copyText(text);
    if(state)state.textContent=t('copied');
  }catch(e){
    if(state)state.textContent=t('copyFailed');
  }
}
async function openMediaSnapshot(){
  showModal(t('mediaSnapshot'),'<div class="modal-loading">'+esc(t('mediaSnapshotLoading'))+'</div>','media-modal',{returnTo:'settings'});
  try{
    var r=await fetch('/api/media-snapshot?date='+encodeURIComponent(selectedDate));
    var d=await r.json();
    if(!r.ok)throw new Error('snapshot failed');
    showModal(t('mediaSnapshot'),
      '<div class="media-snapshot">'+
        '<div class="media-headline">'+esc(d.headline||t('mediaSnapshot'))+'</div>'+
        '<textarea class="media-draft" id="media-draft" spellcheck="false">'+esc(d.draft||'')+'</textarea>'+
        '<div class="media-actions">'+
          '<button class="settings-action" onclick="copyMediaSnapshot()"><b>'+esc(t('copyDraft'))+'</b></button>'+
          '<button class="settings-action" onclick="openSettings()"><b>'+esc(t('backSettings'))+'</b></button>'+
        '</div>'+
        '<div class="media-copy-state" id="media-copy-state"></div>'+
        '<p class="muted"><b>'+esc(t('privacyBoundary'))+'</b> · '+esc(d.privacy_note||'')+'</p>'+
      '</div>',
      'media-modal',
      {returnTo:'settings'}
    );
  }catch(e){
    showModal(t('mediaSnapshot'),'<div class="modal-error">'+esc(t('mediaSnapshotFailed'))+'</div>','media-modal',{returnTo:'settings'});
  }
}
var SETTINGS_DEFAULTS={auto_start:false,background_start:false,silent_start:false,theme:'light',accent:'green',language:'zh',intro_seen:false,ai_provider:'mimo',ai_cloud_enabled:false,ai_api_key:'',ai_base_url:'',ai_model:'',ai_thinking_enabled:false,ai_agent_prompt:'',support_url:'',hidden_apps:[]};
var SETTINGS_STATE=Object.assign({},SETTINGS_DEFAULTS);
var defaultAgentPrompt='';
var settingsLoadPromise=null;
var settingsLoaded=false;
var ACCENT_META={
  green:{color:'#2f8f59',labelKey:'green'},
  rose:{color:'#e75d74',labelKey:'rose'},
  indigo:{color:'#4f68d7',labelKey:'indigo'},
  ink:{color:'#2f3437',labelKey:'ink'}
};
function settingsToggle(key){
  var on=!!SETTINGS_STATE[key];
  return '<button class="switch '+(on?'on':'')+'" onclick="toggleSetting(&quot;'+key+'&quot;)" aria-label="'+(on?t('switchOff'):t('switchOn'))+'"></button>';
}
function languageChoices(){
  var lang=getLang();
  return '<div class="theme-segment">'+
    '<button class="theme-choice '+(lang==='zh'?'active':'')+'" onclick="setLanguage(&quot;zh&quot;)">'+esc(t('chinese'))+'</button>'+
    '<button class="theme-choice '+(lang==='en'?'active':'')+'" onclick="setLanguage(&quot;en&quot;)">'+esc(t('english'))+'</button>'+
  '</div>';
}
function accentChoices(){
  var active=getAccent();
  return '<div class="accent-grid">'+Object.keys(ACCENT_META).map(function(key){
    var item=ACCENT_META[key];
    return '<button class="accent-choice '+(active===key?'active':'')+'" data-accent-choice="'+esc(key)+'" onclick="setAccentChoice(&quot;'+esc(key)+'&quot;)"><i style="--swatch:'+esc(item.color)+'"></i><span>'+esc(t(item.labelKey))+'</span></button>';
  }).join('')+'</div>';
}
function aiProviderChoices(active){
  var choices=[['mimo','MiMo API'],['deepseek','DeepSeek'],['openai','OpenAI compatible']];
  return '<select class="settings-select" id="ai-provider-choice" onchange="saveAiSettings()">'+choices.map(function(item){return '<option value="'+item[0]+'"'+(active===item[0]?' selected':'')+'>'+esc(item[1])+'</option>'}).join('')+'</select>';
}
function settingsNavHtml(){
  var en=getLang()==='en';
  var items=[
    [1,(en?'Appearance':'外观')],
    [2,(en?'Startup':'启动')],
    [3,t('aiSettings')],
    [4,t('support')],
    [5,(en?'Data':'数据')],
    [6,(en?'Privacy':'隐私')],
    [7,(en?'Help':'帮助')]
  ];
  return '<div class="settings-nav" role="navigation" aria-label="'+(en?'Settings sections':'设置分区')+'">'+
    items.map(function(it){return '<a class="settings-nav-chip" href="#" onclick="scrollSettingsSection('+it[0]+');return false">'+esc(it[1])+'</a>'}).join('')+
    '</div>';
}
function scrollSettingsSection(n){
  var el=document.getElementById('settings-sec-'+n);
  if(el)el.scrollIntoView({behavior:'smooth',block:'start'});
}
function renderSettings(settings){
  SETTINGS_STATE=Object.assign({},SETTINGS_DEFAULTS,settings||{});
  SETTINGS_STATE.silent_start=!!(SETTINGS_STATE.silent_start||SETTINGS_STATE.background_start);
  var en=getLang()==='en';
  showModal(en?'Settings':'设置',
    '<div class="settings-panel">'+
      settingsNavHtml()+
      '<div class="settings-section">'+
        '<h3>'+(en?'Email reports':'邮箱与报告')+'</h3>'+
        '<p class="muted">'+(en?'Verify your email and choose weekly or monthly reports.':'验证邮箱，自选每周、每月的数据反馈报告。')+'</p>'+
        '<div class="settings-actions"><button class="settings-action" onclick="openEmailReports()"><b>'+(en?'Email login and subscriptions':'邮箱登录与报告订阅')+'</b></button></div>'+
      '</div>'+
      '<div class="settings-section" id="settings-sec-1">'+
        '<h3>'+(en?'Appearance':'外观')+'</h3>'+
        '<div class="setting-copy"><b>'+(en?'Theme':'明暗')+'</b></div>'+
        '<div class="theme-segment">'+
          '<button class="theme-choice '+(getTheme()==='light'?'active':'')+'" data-theme-choice="light" onclick="setThemeChoice(&quot;light&quot;)">'+(en?'Light':'浅色')+'</button>'+
          '<button class="theme-choice '+(getTheme()==='dark'?'active':'')+'" data-theme-choice="dark" onclick="setThemeChoice(&quot;dark&quot;)">'+(en?'Dark':'深色')+'</button>'+
        '</div>'+
        '<div class="setting-copy"><b>'+esc(t('accent'))+'</b></div>'+
        accentChoices()+
        '<div class="setting-copy"><b>'+esc(t('language'))+'</b></div>'+
        languageChoices()+
      '</div>'+
      '<div class="settings-section" id="settings-sec-2">'+
        '<h3>'+(en?'Startup':'启动方式')+'</h3>'+
        '<div class="setting-row"><div class="setting-copy"><b>'+(en?'Launch at login':'开机自启')+'</b></div>'+settingsToggle('auto_start')+'</div>'+
        '<div class="setting-row"><div class="setting-copy"><b>'+(en?'Silent start':'静默启动')+'</b></div>'+settingsToggle('silent_start')+'</div>'+
      '</div>'+
      '<div class="settings-section" id="settings-sec-3">'+
        '<h3>'+esc(t('aiSettings'))+'</h3>'+
        '<div class="settings-actions">'+
          '<button class="settings-action" onclick="showAiConfigGuide()"><b>'+esc(t('aiGuideOpen'))+'</b></button>'+
          '<button class="settings-action" onclick="showAiConfigIndex()"><b>'+esc(t('aiGuideIndex'))+'</b></button>'+
        '</div>'+
        '<p class="muted">'+esc(t('aiKeyHint'))+'</p>'+
        '<div class="setting-field"><label for="ai-provider-choice">'+esc(t('aiProvider'))+'</label>'+aiProviderChoices(SETTINGS_STATE.ai_provider)+'</div>'+
        '<div class="setting-field"><label for="ai-api-key">'+esc(t('aiApiKey'))+'</label><div class="key-field"><input class="settings-input" type="password" id="ai-api-key" value="'+esc(SETTINGS_STATE.ai_api_key||'')+'" placeholder="'+esc(t('aiApiKeyPlaceholder'))+'" autocomplete="off" spellcheck="false" onchange="saveAiSettings()"><button type="button" class="key-visibility" id="key-visibility-btn" onclick="toggleApiKeyVisibility()" aria-label="'+esc(t('keyShow'))+'">'+esc(t('keyShow'))+'</button></div></div>'+
        '<div class="setting-row"><div class="setting-copy"><b>'+esc(t('agentThinkingLabel'))+'</b></div>'+settingsToggle('ai_thinking_enabled')+'</div>'+
        '<div class="setting-field"><label for="ai-agent-prompt">'+esc(t('aiAgentPromptLabel'))+'</label>'
        +'<textarea class="settings-input ai-prompt-area" id="ai-agent-prompt" rows="9" spellcheck="false" placeholder="'+esc(t('aiAgentPromptHint'))+'" onchange="saveAiSettings()">'+esc(SETTINGS_STATE.ai_agent_prompt||defaultAgentPrompt||'')+'</textarea>'
        +'<p class="muted">'+esc(t('aiAgentPromptHint'))+'</p>'
        +'<button class="settings-action" onclick="resetAgentPrompt()"><b>'+esc(t('aiAgentPromptReset'))+'</b></button>'+
        '<div class="settings-actions">'+
          '<button class="settings-action" onclick="previewAiOutbound()"><b>'+esc(t('aiPreview'))+'</b></button>'+
          '<button class="settings-action" onclick="openExportWithPrompt()"><b>'+esc(t('aiExportPrompt'))+'</b></button>'+
        '</div>'+
      '</div>'+
      '<div class="settings-section" id="settings-sec-4">'+
        '<h3>'+esc(t('support'))+'</h3>'+
        '<p class="muted">'+esc(t('supportDesc'))+'</p>'+
        '<div class="setting-field"><label for="support-url-input">'+esc(t('supportUrl'))+'</label><input class="settings-input" id="support-url-input" type="url" value="'+esc(SETTINGS_STATE.support_url||'')+'" placeholder="'+esc(t('supportUrlPlaceholder'))+'" autocomplete="url" spellcheck="false" onchange="saveSupportUrl()"></div>'+
        '<div class="settings-actions"><button class="settings-action" onclick="openSupport()"><b>'+esc(t('supportOpen'))+'</b></button></div>'+
      '</div>'+
      '<div class="settings-section" id="settings-sec-5">'+
        '<h3>'+(en?'Data & Version':'数据与版本')+'</h3>'+
        '<p class="muted">'+(en?'In-app updates are disabled in this source build.':'当前源码构建未启用应用内更新。')+'</p>'+
        '<p class="muted" style="font-size:11px;word-break:break-all">'+esc(t('dataRootLabel'))+'：<b>'+esc(SETTINGS_STATE.data_root||'data')+'</b><br>'+esc(t('dataRootMigrate'))+'</p>'+
        '<div class="settings-actions">'+
          '<button class="settings-action" onclick="openExport()"><b>'+(en?'Export':'导出数据')+'</b></button>'+
          '<button class="settings-action" onclick="openMediaSnapshot()"><b>'+esc(t('mediaSnapshot'))+'</b></button>'+
          '<button class="settings-action danger" onclick="deleteHistoricalWindowTitles()"><b>'+(en?'Delete legacy titles':'清除历史窗口标题')+'</b></button>'+
          '<button class="settings-action" onclick="compactDatabase()"><b>'+(en?'Compact database':'压缩数据库')+'</b></button>'+
        '</div>'+
        '<h4 style="margin-top:12px;font-size:13px;color:var(--text2)">'+(en?'Data protection':'数据保护')+'</h4>'+
        '<p class="muted">'+(en?'Backups are saved locally under data/backups. Recovery overwrites current data.':'备份保存在 data/backups/ 下；恢复会覆盖当前数据。')+'</p>'+
        '<div class="settings-actions">'+
          '<button class="settings-action" onclick="createDataBackup()"><b>'+(en?'Create backup':'创建备份')+'</b></button>'+
          '<button class="settings-action" onclick="openDataBackupManager()"><b>'+(en?'Manage backups':'管理备份')+'</b></button>'+
        '</div>'+
      '</div>'+
      '<div class="settings-section" id="settings-sec-6">'+
        '<h3>'+(en?'Privacy':'隐私')+'</h3>'+
        '<p class="muted">'+esc(t('hiddenAppsHint'))+'</p>'+
        '<input class="settings-input" id="hidden-apps-input" type="text" value="'+esc((SETTINGS_STATE.hidden_apps||[]).join(', '))+'" placeholder="'+esc(t('hiddenAppsPlaceholder'))+'" spellcheck="false">'+
        '<div class="settings-actions"><button class="settings-action" onclick="saveHiddenApps()"><b>'+esc(t('hiddenAppsSave'))+'</b></button></div>'+
      '</div>'+
      '<div class="settings-section" id="settings-sec-7">'+
        '<h3>'+(en?'Help':'帮助')+'</h3>'+
        '<p class="muted">'+esc(t('feedbackDesc'))+'</p>'+
        '<div class="settings-actions">'+
          '<button class="settings-action" onclick="openMetricRegistry()"><b>'+esc(t('metricRegistryOpen'))+'</b></button>'+
          '<button class="settings-action" onclick="openCollectorBasis()"><b>'+esc(t('collectorBasisOpen'))+'</b></button>'+
          '<button class="settings-action" onclick="openFeedback()"><b>'+esc(t('feedbackOpen'))+'</b></button>'+
        '</div>'+
      '</div>'+
    '</div>',
    'settings-modal',
    {returnTo:null}
  );
}
async function loadSettingsQuietly(updateModal){
  if(settingsLoadPromise)return settingsLoadPromise;
  settingsLoadPromise=fetch('/api/settings').then(function(r){return r.json()}).then(function(d){
    if(!d.ok)throw new Error(d.error||'设置读取失败');
    SETTINGS_STATE=Object.assign({},SETTINGS_DEFAULTS,d.settings||{});
    SETTINGS_STATE.silent_start=!!(SETTINGS_STATE.silent_start||SETTINGS_STATE.background_start);
    if(d.settings&&d.settings.language&&d.settings.language!==getLang()){
      localStorage.setItem('kx-lang',d.settings.language);
      applyLanguage();
    }
    if(d.settings&&d.settings.theme&&d.settings.theme!==getTheme()){
      localStorage.setItem('kx-theme',d.settings.theme);
    }
    if(d.settings&&d.settings.accent&&d.settings.accent!==getAccent()){
      localStorage.setItem('kx-accent',d.settings.accent);
    }
    applyTheme(getTheme());
    settingsLoaded=true;
    if(updateModal&&!document.getElementById('modal').hidden&&document.getElementById('modal-title').textContent===t('settings')){
      renderSettings(SETTINGS_STATE);
    }
    return SETTINGS_STATE;
  }).finally(function(){
    settingsLoadPromise=null;
    refreshDefaultAgentPrompt();
  });
  return settingsLoadPromise;
}
async function openSettings(){
  renderSettings(SETTINGS_STATE);
  try{
    await loadSettingsQuietly(true);
  }catch(e){
    if(!settingsLoaded)showModal(t('settings'),'<div class="modal-error">'+esc(e.message||t('settingsReadFailed'))+'</div>');
  }
}
async function emailReportRequest(action,body){
  var r=await fetch('/api/email-reports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({},body||{},{action:action}))});
  var d=await r.json();
  if(!r.ok||!d.ok)throw new Error(d.error||'邮件操作失败');
  return d;
}
function emailField(id,label,value,type,placeholder){
  return '<div class="setting-field"><label for="'+id+'">'+label+'</label><input class="settings-input" id="'+id+'" type="'+(type||'text')+'" value="'+esc(value||'')+'" placeholder="'+esc(placeholder||'')+'" autocomplete="off"></div>';
}
function renderEmailReports(d,notice){
  var smtp=d.smtp||{},subs=d.subscriptions||{},agent=d.transport==='agently';
  var statuses={sent:'发信服务已接收',uncertain:'投递待确认',sending:'投递待确认'};
  var body='<div class="email-panel"><p class="muted">本机邮箱登录用于验证报告收件地址。登录状态保留 90 天；统计数据库保存在本机。</p>'+
    '<p id="email-notice" role="status" aria-live="polite">'+esc(notice||d.error||'')+'</p>'+
    '<details '+(!d.configured?'open':'')+'><summary>发信邮箱设置'+(d.configured?' · 已保存':' · 尚未配置')+'</summary>'+
    '<div class="setting-field"><label for="mail-transport">发信方式</label><select class="settings-input" id="mail-transport" onchange="chooseEmailTransport(this.value)"><option value="smtp" '+(!agent?'selected':'')+'>普通邮箱 SMTP</option><option value="agently" '+(agent?'selected':'')+'>QQ Agent 邮箱</option></select></div>'+
    emailField('mail-sender','发件邮箱',smtp.sender,'email','完整发件邮箱地址')+
    '<div id="mail-agently-fields" '+(!agent?'hidden':'')+'><p class="muted">使用数字节律独立工作区中已授权的 QQ Agent 邮箱，无需 SMTP 授权码。保存时和每次发送前都会核对发件地址。更换发信方式或地址后，需要重新选择报告订阅。</p></div>'+
    '<div id="mail-smtp-fields" '+(agent?'hidden':'')+'>'+
    '<p class="muted">已预填 QQ 邮箱参数。请在 QQ 邮箱开启 SMTP 服务，填入生成的授权码；授权码只在本机保存，不会回显。其他邮箱可修改下方参数。</p>'+
    emailField('mail-host','SMTP 主机',smtp.host||'smtp.qq.com','text','smtp.qq.com')+
    emailField('mail-port','端口',smtp.port||465,'number','465')+
    '<div class="setting-field"><label for="mail-security">加密连接</label><select class="settings-input" id="mail-security"><option value="ssl" '+(smtp.security!=='starttls'?'selected':'')+'>SSL/TLS（通常为 465）</option><option value="starttls" '+(smtp.security==='starttls'?'selected':'')+'>STARTTLS（通常为 587）</option></select></div>'+
    emailField('mail-username','SMTP 用户名（留空使用发件邮箱）',smtp.username)+
    emailField('mail-password','授权码／应用密码','','password',d.configured?'留空保留现有授权码':'请在此输入，不要发到聊天中')+
    '</div><div class="settings-actions"><button class="settings-action" onclick="saveEmailTransport()"><b>保存发信配置</b></button></div></details>';
  if(d.logged_in){
    body+='<h3>已登录：'+esc(d.email)+'</h3><button class="settings-action" onclick="emailReportAction(\'logout\',{})">退出邮箱登录并停止订阅</button>'+
      '<h3>报告订阅</h3><p class="muted">周报：周一 09:00 回顾上周；月报：每月 1 日 09:00 回顾上月。按本机时间，关闭软件期间下次启动补发。订阅从当前周／月开始。</p>'+
      '<div class="email-options"><label><input type="checkbox" id="mail-weekly" '+(subs.weekly&&subs.weekly.enabled?'checked':'')+'> 每周报告</label><label><input type="checkbox" id="mail-monthly" '+(subs.monthly&&subs.monthly.enabled?'checked':'')+'> 每月报告</label><label>周起始日 <select id="mail-week-start">'+weekStartOptions(d.preferences&&d.preferences.week_start||1)+'</select></label><p class="muted">默认周一上午发送上一整周（周一至周日）；调整后按所选日为一周之始，已开启的订阅从新锚点重新起算，不回补重叠窗口。</p></div>'+
      '<label class="email-consent"><input type="checkbox" id="mail-consent"> 我同意将日期、键鼠日总量、前台时长与采集覆盖，通过上述发信服务发送到已验证邮箱。邮件不包含应用名、标签、路径、输入内容或原始事件。</label>'+
      '<div class="settings-actions"><button class="settings-action" onclick="saveEmailSubscription()"><b>保存订阅</b></button><button class="settings-action" onclick="previewEmailReport(\'weekly\')">预览上周报告</button><button class="settings-action" onclick="previewEmailReport(\'monthly\')">预览上月报告</button></div>'+
      '<h3>最近投递</h3><p class="muted">“发信服务已接收”不代表已经进入收件箱，请同时检查垃圾邮件。</p>'+
      ((d.deliveries||[]).length?d.deliveries.map(function(item){return '<div class="email-delivery"><b>'+esc(item.kind==='weekly'?'周报':'月报')+' · '+esc(item.start)+' — '+esc(item.end)+'</b><p>'+esc(statuses[item.status]||item.status)+'</p>'+(item.error?'<p class="muted">'+esc(item.error)+'</p>':'')+(item.status!=='sent'?'<button class="settings-action" data-delivery-id="'+esc(item.id)+'" onclick="retryEmailReport(this.dataset.deliveryId)">确认未收到后重试</button>':'')+'</div>';}).join(''):'<p class="muted">暂无投递。可先预览并手动发送最近一份报告。</p>');
  }else{
    body+='<h3>邮箱验证码登录</h3>'+emailField('mail-recipient','收件邮箱',d.recipient_hint||'','email')+
      '<div class="settings-actions"><button class="settings-action" onclick="requestEmailCode()"><b>发送验证码</b></button></div>'+
      emailField('mail-code','验证码','','text','6 位数字，10 分钟内有效')+
      '<div class="settings-actions"><button class="settings-action" onclick="verifyEmailCode()"><b>验证并登录</b></button></div><p class="muted">退出登录后自动发送停止；再次登录后需要重新选择订阅。</p>';
  }
  showModal('邮箱登录与报告订阅',body+'</div>','settings-modal',{returnTo:'settings'});
}
async function openEmailReports(){
  try{renderEmailReports(await emailReportRequest('status'));}
  catch(e){showModal('邮箱与报告','<p class="modal-error">'+esc(e.message)+'</p>','settings-modal',{returnTo:'settings'});}
}
function emailValue(id){var node=document.getElementById(id);return node?node.value:'';}
async function emailReportAction(action,body){
  var panel=document.querySelector('.email-panel');
  if(!panel)return;
  var buttons=panel.querySelectorAll('button');
  buttons.forEach(function(b){b.disabled=true;});
  try{
    var d=await emailReportRequest(action,body);
    if(!panel.isConnected)return;
    if(action==='request_code'){
      document.getElementById('email-notice').textContent=d.message;
    }else{renderEmailReports(d,action==='subscribe'?'订阅已保存':action==='configure'?'发信配置已保存，请获取验证码验证连接':'');}
  }catch(e){if(panel.isConnected)document.getElementById('email-notice').textContent=e.message;}
  finally{buttons.forEach(function(b){b.disabled=false;});}
}
function chooseEmailTransport(value){document.getElementById('mail-agently-fields').hidden=value!=='agently';document.getElementById('mail-smtp-fields').hidden=value==='agently';}
function saveEmailTransport(){emailReportAction('configure',{transport:emailValue('mail-transport'),recipient_hint:emailValue('mail-recipient').trim(),host:emailValue('mail-host'),port:Number(emailValue('mail-port')),security:emailValue('mail-security'),sender:emailValue('mail-sender'),username:emailValue('mail-username'),password:emailValue('mail-password')});}
function requestEmailCode(){emailReportAction('request_code',{email:emailValue('mail-recipient').trim()});}
function verifyEmailCode(){emailReportAction('verify',{email:emailValue('mail-recipient').trim(),code:emailValue('mail-code').trim()});}
function saveEmailSubscription(){emailReportAction('subscribe',{weekly:document.getElementById('mail-weekly').checked,monthly:document.getElementById('mail-monthly').checked,consent:document.getElementById('mail-consent').checked,week_start:Number(emailValue('mail-week-start')||1)});}
function weekStartOptions(selected){var names=['周一','周二','周三','周四','周五','周六','周日'],out='';for(var i=1;i<=7;i++){out+='<option value="'+i+'"'+(Number(selected)===i?' selected':'')+'>'+names[i-1]+'</option>';}return out;}
function retryEmailReport(id){if(confirm('请先检查收件箱及垃圾邮件。确认未收到后再重试；网络中断可能发生在邮件已被接收之后，重试可能重复投递。'))emailReportAction('retry',{id:id,confirm_possible_duplicate:true});}
async function previewEmailReport(kind){
  try{
    var d=await emailReportRequest('preview',{kind:kind});
    showModal('报告预览','<div class="email-panel"><p class="muted">以下为将发送的完整邮件。仅发送到已验证邮箱。预览 10 分钟内有效。</p><iframe class="email-preview" sandbox="" title="邮件正文预览" srcdoc="'+esc(d.report.html)+'"></iframe><label class="email-consent"><input type="checkbox" id="mail-send-consent"> 我同意将这份汇总报告通过已配置的发信服务发送至已验证邮箱。</label><div class="settings-actions"><button class="settings-action" data-preview-token="'+esc(d.preview_token)+'" onclick="sendPreviewedEmail(this.dataset.previewToken)">发送这份报告</button><button class="settings-action" onclick="openEmailReports()">返回订阅设置</button></div><p id="email-notice" role="status"></p></div>','settings-modal',{returnTo:'settings'});
  }catch(e){var n=document.getElementById('email-notice');if(n)n.textContent=e.message;}
}
function sendPreviewedEmail(token){emailReportAction('send_latest',{preview_token:token,consent:document.getElementById('mail-send-consent').checked});}
async function deleteHistoricalWindowTitles(){
  var en=getLang()==='en';
  var message=en?'Delete all historical window titles stored by older builds?':'确定清除旧版本保存的全部窗口标题吗？';
  if(!window.confirm(message))return;
  try{
    var r=await fetch('/api/privacy/window-titles/delete',{method:'POST'});
    var d=await r.json();
    if(!r.ok||d.ok===false)throw new Error(d.error||'delete failed');
    showModal(en?'Legacy titles removed':'历史窗口标题已清除','<div class="modal-loading">'+(en?'Removed rows: ':'已清除记录：')+esc(d.deleted_rows||0)+'</div>','settings-modal',{returnTo:'settings'});
  }catch(e){
    showModal(en?'Delete failed':'清除失败','<div class="modal-error">'+esc(e.message||'delete failed')+'</div>','settings-modal',{returnTo:'settings'});
  }
}
async function compactDatabase(){
  var en=getLang()==='en';
  if(!window.confirm(en?'Compact the database now? Recording pauses for a few seconds.':'立即压缩数据库？期间记录会暂停几秒钟。'))return;
  try{
    var r=await fetch('/api/db/compact',{method:'POST'});
    var d=await r.json();
    if(!r.ok||d.ok===false)throw new Error(d.error||(en?'Compaction failed':'压缩失败'));
    var beforeMB=d.before_bytes?Math.round(d.before_bytes/1024/1024):0;
    var afterMB=d.after_bytes?Math.round(d.after_bytes/1024/1024):0;
    showModal(en?'Database compacted':'数据库已压缩','<div class="modal-loading">'+(en?'Size: ':'大小：')+esc(beforeMB)+' MB → '+esc(afterMB)+' MB</div>','settings-modal',{returnTo:'settings'});
  }catch(e){
    showModal(en?'Compaction failed':'压缩失败','<div class="modal-error">'+esc(e.message||(en?'Compaction failed':'压缩失败'))+'</div>','settings-modal',{returnTo:'settings'});
  }
}
async function saveHiddenApps(){
  var en=getLang()==='en';
  var box=document.getElementById('hidden-apps-input');
  var list=(box&&box.value||'').split(',').map(function(s){return String(s||'').trim()}).filter(Boolean);
  try{
    var r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hidden_apps:list})});
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||t('settingsSaveFailed'));
    SETTINGS_STATE=Object.assign({},SETTINGS_STATE,d.settings||{});
    renderSettings(SETTINGS_STATE);
    loadApps();
  }catch(e){
    showModal(t('settings'),'<div class="modal-error">'+esc(e.message||t('settingsSaveFailed'))+'</div>');
  }
}
async function createDataBackup(){
  var en=getLang()==='en';
  try{
    var r=await fetch('/api/data/backup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:''})});
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||(en?'Backup failed':'备份失败'));
    showModal(t('settings'),'<div class="agent-shell"><p>'+(en?'Backup created: ':'备份已创建：')+esc(d.backup.id)+'</p><p class="muted">'+(en?'Saved to: ':'保存位置：')+esc(d.backup.path)+'</p></div>');
  }catch(e){
    showModal(t('settings'),'<div class="modal-error">'+esc(e.message||(en?'Backup failed':'备份失败'))+'</div>');
  }
}
async function openDataBackupManager(){
  var en=getLang()==='en';
  try{
    var r=await fetch('/api/data/backups');
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||(en?'Failed to load backups':'加载备份失败'));
    var backups=(d.backups||[]);
    var html='<div class="agent-shell"><p class="muted">'+(en?'Local backup directory: ':'本地备份目录：')+esc(d.backup_root)+'</p>'+(backups.length?'<div class="backup-list">':'<p>'+(en?'No backups yet.':'暂无备份。')+'</p>')+
      backups.map(function(b){
        return '<div class="backup-item">'+
          '<div><b>'+esc(b.name||b.id)+'</b></div>'+
          '<div class="muted" style="font-size:12px">'+esc(b.created_at_iso)+' · v'+esc(b.version)+' · '+Math.round(b.size/1024)+' KB · '+esc(b.source)+'</div>'+
          '<div class="settings-actions" style="margin-top:8px">'+
            '<button class="settings-action" onclick="restoreDataBackup(\''+esc(b.id)+'\')"><b>'+(en?'Restore':'恢复')+'</b></button>'+
            '<button class="settings-action danger" onclick="deleteDataBackup(\''+esc(b.id)+'\')"><b>'+(en?'Delete':'删除')+'</b></button>'+
          '</div>'+
        '</div>';
      }).join('')+(backups.length?'</div>':'')+'</div>';
    showModal((en?'Manage backups':'管理备份'),html,'agent-modal');
  }catch(e){
    showModal(t('settings'),'<div class="modal-error">'+esc(e.message||(en?'Failed to load backups':'加载备份失败'))+'</div>');
  }
}
async function restoreDataBackup(backupId){
  var en=getLang()==='en';
  if(!confirm(en?'Restore will overwrite current data. A pre-restore snapshot will be created automatically. Continue?':'恢复会覆盖当前数据，系统会自动创建恢复前快照。继续吗？'))return;
  try{
    var r=await fetch('/api/data/restore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({backup_id:backupId})});
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||(en?'Restore failed':'恢复失败'));
    showModal(t('settings'),'<div class="agent-shell"><p>'+(en?'Restored: ':'已恢复：')+esc(d.backup.id)+'</p><p class="muted">'+(en?'Please restart the app if data appears stale.':'如数据未刷新，请重启应用。')+'</p></div>');
  }catch(e){
    showModal(t('settings'),'<div class="modal-error">'+esc(e.message||(en?'Restore failed':'恢复失败'))+'</div>');
  }
}
async function deleteDataBackup(backupId){
  var en=getLang()==='en';
  if(!confirm(en?'Delete this backup permanently?':'永久删除这个备份吗？'))return;
  try{
    var r=await fetch('/api/data/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({backup_id:backupId})});
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||(en?'Delete failed':'删除失败'));
    openDataBackupManager();
  }catch(e){
    showModal(t('settings'),'<div class="modal-error">'+esc(e.message||(en?'Delete failed':'删除失败'))+'</div>');
  }
}
var METRIC_EXPLAIN={
  'IR-EVT-001':{zh:'统计键盘按下事件的数量，不记录任何按键内容，是最基础的输入量。',en:'Count of captured keyboard-down events; content is never recorded. The most basic input volume.'},
  'IR-EVT-002':{zh:'统计鼠标按键按下事件的数量，不记录位置。',en:'Count of captured mouse-button events; positions are never recorded.'},
  'IR-EVT-003':{zh:'统计滚轮滚动事件的数量，反映浏览类操作。',en:'Count of scroll-wheel events, reflecting browsing-like activity.'},
  'IR-EVT-004':{zh:'键盘、点击、滚轮三者合计，代表一天里触发的输入总量。',en:'Sum of keyboard, click, and scroll events; the day\'s total input volume.'},
  'IR-FG-001':{zh:'某进程处于前台的总秒数；锁屏、睡眠等非前台状态不计入。',en:'Total seconds a process stays in the foreground; locked or sleeping time is excluded.'},
  'IR-HLT-001':{zh:'采集器认为自己应该在岗的时长，包括正常、降级和未知状态。',en:'Time the collector should have been responsible, including healthy, degraded, and unknown states.'},
  'IR-HLT-002':{zh:'采集器正常运行记录的时长。',en:'Time recorded while the collector ran normally.'},
  'IR-HLT-003':{zh:'健康时长除以应有时长；越接近 1 代表记录越连续。',en:'Healthy time divided by expected time; closer to 1 means more continuous coverage.'},
  'IR-HLT-004':{zh:'采集器无法确认状态的时长；出现较多会拉低记录覆盖。',en:'Time when the collector could not confirm state; frequent gaps lower coverage.'},
  'IR-DER-001':{zh:'至少发生一次输入事件的本地分钟数，描述“活跃分钟”而非总量。',en:'Distinct local minutes with at least one input event; describes active minutes, not volume.'},
  'IR-DER-002':{zh:'每分钟输入事件数，除以健康观察分钟，衡量记录的输入强度。',en:'Input events per minute over healthy observation minutes; input intensity.'},
  'IR-DER-003':{zh:'前台期间的每分钟输入事件数，衡量用某个应用时的专注强度。',en:'Input events per covered foreground minute; intensity while an app is in the foreground.'},
  'IR-DER-004':{zh:'按本地小时分组的输入事件分布，用来读一天里的节奏。',en:'Input events grouped by local hour, for reading daily rhythm.'},
  'IR-SES-001':{zh:'同一进程的连续前台区间，中间没有锁屏、睡眠或未知状态。',en:'A continuous same-process foreground interval with no lock, sleep, or unknown span.'},
  'IR-SES-002':{zh:'前台进程从 A 变成 B 的次数，描述切换频率。',en:'Count of foreground process changes where A != B; describes switching frequency.'},
  'IR-SES-003':{zh:'离开某个应用后再次回到它的间隔；间隔越短越像高频往返。',en:'Time from leaving a process until the next return to it; short gaps mean frequent back-and-forth.'},
  'IR-BAS-001':{zh:'过去 28 天里同类型日（工作日/休息日）的中位数、P25-P75 和 IQR/MAD，是“你的常态”。',en:'Median, P25-P75, IQR/MAD over the last 28 same-type days (weekday/weekend); your typical range.'},
  'IR-BAS-002':{zh:'今天的值落在本人典型范围的哪个区间：低于、在区间内，还是高于。',en:'Where today\'s value sits relative to your typical range: below, within, or above.'},
  'IR-BAS-003':{zh:'用中位数和 MAD 衡量今天偏离常态的程度，对异常值不敏感。',en:'How far today deviates from your norm, measured with median and MAD; robust to outliers.'}
};
function metricExplain(id,en){var e=METRIC_EXPLAIN[id];return e?(e[en?'en':'zh']||e.zh):(en?'No plain-language note yet for this metric.':'这个指标暂时没有通俗解释。')}
async function openMetricRegistry(){
  showModal(t('metricRegistryTitle'),'<div class="modal-loading">'+esc(t('agentObservationLoading'))+'</div>','agent-modal');
  try{
    var r=await fetch('/api/metric-registry');
    var d=await r.json();
    var metrics=(d&&d.metrics)||{};
    var en=getLang()==='en';
    var html='<div class="agent-shell"><div class="agent-summary">'+esc(d.metric_version?t('metricRegistryVersion')+' '+d.metric_version:'')+'</div><div class="metric-help-list">'+
      Object.keys(metrics).sort().map(function(id){
        var m=metrics[id];
        return '<div class="metric-help-item"><div class="metric-help-head"><b>'+esc(id)+'</b><span>'+esc(m.name||'')+'</span></div><p>'+esc(metricExplain(id,en))+'</p><div class="metric-help-meta">'+esc(m.unit||'')+(m.status?' · '+esc(m.status):'')+'</div></div>';
      }).join('')+
    '</div></div>';
    showModal(t('metricRegistryTitle'),html,'agent-modal');
  }catch(e){
    showModal(t('metricRegistryTitle'),'<div class="modal-error">'+esc(e.message||t('settingsReadFailed'))+'</div>','agent-modal');
  }
}
async function openCollectorBasis(){
  var en=getLang()==='en';
  var items=[
    en?['Collection granularity','Input events are aggregated in 1-second batches; content, coordinates, and screenshots are never recorded.']:['采集粒度','输入事件按 1 秒批处理汇总，不记录内容、坐标或截图。'],
    en?['Foreground states','Foreground is process-level. Lock, sleep, pause, and session disconnect switch the collector state and split intervals.']:['前台状态','前台按进程级实体识别；锁屏、睡眠、暂停、会话断开会切换采集状态并切分区间。'],
    en?['Gap recovery','System suspend/resume is validated with the monotonic clock; an unobserved gap over 15 seconds becomes an unknown segment and is excluded from foreground time.']:['间隙恢复','系统挂起/恢复用单调时钟校验；超过 15 秒的未观察间隙记为 unknown 段，不计入前台时长。'],
    en?['AFK','This version has no "no input for N minutes = away" parameter. No input does not mean away; only system-state changes split intervals.']:['AFK','当前版本没有“无输入 X 分钟即视为离开”的参数。无输入不等于离开，只有系统状态变化才切分区间。'],
    en?['Sensitivity','A configurable AFK threshold would change the meaning of active time, coverage, and return intervals at once, and break comparability with existing records. It is therefore left to a separate collector design.']:['敏感性','可配置的 AFK 阈值会同时改变活跃时长、记录覆盖和回返间隔的口径，并与既有记录失去可比性，因此留待单独的采集器设计，暂未提供。']
  ];
  var html='<div class="agent-shell">'+items.map(function(item){
    return '<div class="metric-help-item"><div class="metric-help-head"><b>'+esc(item[0])+'</b></div><p>'+esc(item[1])+'</p></div>';
  }).join('')+'</div>';
  showModal(t('collectorBasisTitle'),html,'agent-modal');
}
function feedbackCategoryOptions(active){
  var items=[
    ['bug',t('feedbackBug')],['idea',t('feedbackIdea')],['question',t('feedbackQuestion')],['praise',t('feedbackPraise')],['other',t('feedbackOther')]
  ];
  return '<select class="settings-select" id="feedback-category">'+items.map(function(item){
    return '<option value="'+esc(item[0])+'"'+(active===item[0]?' selected':'')+'>'+esc(item[1])+'</option>';
  }).join('')+'</select>';
}
function openFeedback(){
  var html='<div class="settings-panel"><div class="settings-section">'+
    '<p class="muted">'+esc(t('feedbackDesc'))+'</p>'+
    '<div class="setting-field"><label for="feedback-category">'+esc(t('feedbackCategory'))+'</label>'+feedbackCategoryOptions('bug')+'</div>'+
    '<div class="setting-field"><label for="feedback-subject">'+esc(t('feedbackSubject'))+'</label><input class="settings-input" id="feedback-subject" type="text" maxlength="200" placeholder="'+esc(t('feedbackSubjectPlaceholder'))+'" spellcheck="false"></div>'+
    '<div class="setting-field"><label for="feedback-message">'+esc(t('feedbackMessage'))+'</label><textarea class="settings-input ai-prompt-area" id="feedback-message" rows="7" maxlength="4000" spellcheck="false" placeholder="'+esc(t('feedbackMessagePlaceholder'))+'"></textarea></div>'+
    '<div class="setting-field"><label for="feedback-contact">'+esc(t('feedbackContact'))+'</label><input class="settings-input" id="feedback-contact" type="text" maxlength="200" placeholder="'+esc(t('feedbackContactPlaceholder'))+'" spellcheck="false"></div>'+
    '<p class="muted">'+esc(t('feedbackPrivacyNote'))+'</p>'+
    '<div class="settings-actions">'+
      '<button class="settings-action" onclick="saveFeedback()"><b>'+esc(t('feedbackSave'))+'</b></button>'+
      '<button class="settings-action" onclick="exportFeedback()"><b>'+esc(t('feedbackExport'))+'</b></button>'+
    '</div>'+
  '</div></div>';
  showModal(t('feedbackTitle'),html,'feedback-modal',{returnTo:'settings'});
}
async function saveFeedback(){
  var subject=(document.getElementById('feedback-subject')||{}).value||'';
  var message=(document.getElementById('feedback-message')||{}).value||'';
  if(!subject.trim()||!message.trim()){
    showModal(t('feedbackTitle'),'<div class="modal-error">'+esc(t('feedbackRequired'))+'</div>','feedback-modal',{returnTo:'settings'});
    return;
  }
  var payload={
    category:(document.getElementById('feedback-category')||{}).value||'other',
    subject:subject.trim(),
    message:message.trim(),
    contact:(document.getElementById('feedback-contact')||{}).value||''
  };
  try{
    var r=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    var d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||t('feedbackSaveFailed'));
    showModal(t('feedbackTitle'),'<div class="agent-shell"><p>'+esc(t('feedbackSaved'))+'</p><p class="muted">'+esc(t('feedbackPrivacyNote'))+'</p></div>','feedback-modal',{returnTo:'settings'});
  }catch(e){
    showModal(t('feedbackTitle'),'<div class="modal-error">'+esc(e.message||t('feedbackSaveFailed'))+'</div>','feedback-modal',{returnTo:'settings'});
  }
}
async function exportFeedback(){
  try{
    var r=await fetch('/api/feedback/export');
    if(!r.ok)throw new Error(t('feedbackExportFailed'));
    var blob=await r.blob();
    var a=document.createElement('a');
    var url=URL.createObjectURL(blob);
    a.href=url;
    a.download='interaction-rhythm-feedback.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }catch(e){
    showModal(t('feedbackTitle'),'<div class="modal-error">'+esc(e.message||t('feedbackExportFailed'))+'</div>','feedback-modal',{returnTo:'settings'});
  }
}
async function toggleSetting(key){
  var patch={};
  patch[key]=!SETTINGS_STATE[key];
  if(key==='silent_start')patch.background_start=patch[key];
  try{
    var r=await fetch('/api/settings',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(patch)
    });
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||t('settingsSaveFailed'));
    renderSettings(d.settings||{});
  }catch(e){
    showModal(t('settings'),'<div class="modal-error">'+esc(e.message||t('settingsSaveFailed'))+'</div>');
  }
}
async function refreshDefaultAgentPrompt(){
  try{
    var r=await fetch('/api/ai/config');
    var d=await r.json();
    if(d&&d.ok&&d.agent_prompt)defaultAgentPrompt=d.agent_prompt;
  }catch(e){}
}
async function resetAgentPrompt(){
  var box=document.getElementById('ai-agent-prompt');
  if(box)box.value='';
  await saveAiSettings();
}
async function saveAiSettings(){
  var patch={
    ai_provider:(document.getElementById('ai-provider-choice')||{}).value||SETTINGS_STATE.ai_provider,
    ai_api_key:(document.getElementById('ai-api-key')||{}).value||'',
    ai_agent_prompt:(document.getElementById('ai-agent-prompt')||{}).value||''
  };
  try{
    var r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)});
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||t('settingsSaveFailed'));
    renderSettings(d.settings||{});
  }catch(e){showModal(t('settings'),'<div class="modal-error">'+esc(e.message||t('settingsSaveFailed'))+'</div>')}
}
function validSupportUrl(value){
  value=String(value||'').trim();
  if(!value)return true;
  if(!/^https?:\/\//i.test(value))return false;
  try{
    var u=new URL(value);
    return (u.protocol==='http:'||u.protocol==='https:')&&!!u.hostname;
  }catch(e){return false}
}
async function saveSupportUrl(){
  var box=document.getElementById('support-url-input');
  var value=(box&&box.value||'').trim();
  if(!validSupportUrl(value)){
    showModal(t('support'),'<div class="modal-error">'+esc(t('supportInvalid'))+'</div>','settings-modal',{returnTo:'settings'});
    return;
  }
  try{
    var r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({support_url:value})});
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||t('settingsSaveFailed'));
    SETTINGS_STATE=Object.assign({},SETTINGS_STATE,d.settings||{});
    renderSettings(SETTINGS_STATE);
  }catch(e){showModal(t('settings'),'<div class="modal-error">'+esc(e.message||t('settingsSaveFailed'))+'</div>')}
}
async function openSupport(){
  var value=String((SETTINGS_STATE&&SETTINGS_STATE.support_url)||'').trim();
  if(!value){
    showModal(t('support'),'<div class="modal-error">'+esc(t('supportNotConfigured'))+'</div><div class="settings-actions"><button class="settings-action" onclick="closeModal();openSettings()"><b>'+esc(t('settings'))+'</b></button></div>','settings-modal',{returnTo:null});
    return;
  }
  if(!validSupportUrl(value)){
    showModal(t('support'),'<div class="modal-error">'+esc(t('supportInvalid'))+'</div>','settings-modal',{returnTo:null});
    return;
  }
  try{
    if(window.pywebview&&window.pywebview.api&&window.pywebview.api.open_url){
      var opened=await window.pywebview.api.open_url(value);
      if(opened)return;
    }
    window.open(value,'_blank','noopener,noreferrer');
  }catch(e){window.open(value,'_blank','noopener,noreferrer')}
}
async function previewAiOutbound(){
  try{
    var r=await fetch('/api/ai/preview?date='+encodeURIComponent(selectedDate));
    var d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||t('aiPreviewFailed'));
    var fields=(d.fields||[]).map(function(x){
      var value=d.payload&&Object.prototype.hasOwnProperty.call(d.payload,x)?d.payload[x]:null;
      var rendered=value===null||value===undefined?'—':JSON.stringify(value);
      return '<div class="insight-item"><b>'+esc(x)+'</b><div class="ai-preview-value">'+esc(rendered)+'</div></div>';
    }).join('');
    var excluded=(d.excluded_fields||[]).map(function(x){return '<div class="insight-item">'+esc(x)+'</div>'}).join('');
    showModal(t('aiPreviewTitle'),'<div class="insight-foot">'+(d.cloud_enabled?'':' · '+esc(t('aiPreviewUnavailable')))+ '</div><div class="insight-section"><h3>'+esc(t('aiPreviewFields'))+'</h3><div class="insight-list">'+fields+'</div></div><div class="insight-section"><h3>'+esc(t('aiPreviewExcluded'))+'</h3><div class="insight-list">'+excluded+'</div></div>','ai-preview-modal',{returnTo:'settings'});
  }catch(e){showModal(t('aiPreviewTitle'),'<div class="modal-error">'+esc(e.message||t('aiPreviewFailed'))+'</div>','ai-preview-modal',{returnTo:'settings'})}
}
var lastUpdateManifest=null;
var startupUpdateChecked=false;
function renderUpdateNotes(notes){
  if(!notes||!notes.length)return '<p class="muted">'+esc(t('noUpdateNotes'))+'</p>';
  return '<div class="insight-list">'+notes.map(function(x){
    return '<div class="insight-item">'+esc(x)+'</div>';
  }).join('')+'</div>';
}
function showUpdateToast(m){
  lastUpdateManifest=m;
  var title=document.getElementById('update-toast-title');
  var sub=document.getElementById('update-toast-sub');
  if(title)title.textContent=t('updateFound')+' '+(m.latest||'');
  if(sub)sub.textContent=(m.notes&&m.notes[0])?m.notes[0]:t('canInstallLater');
  document.getElementById('update-toast').hidden=false;
}
function dismissUpdateToast(){
  document.getElementById('update-toast').hidden=true;
}
function showUpdateAvailable(m,returnTo){
  lastUpdateManifest=m;
  var size=m.size?(' · '+(m.size/1024/1024).toFixed(1)+' MB'):'';
  showModal(t('updateFound'),
    '<div class="metric-grid">'+
      '<div class="metric"><span>'+esc(t('currentVersion'))+'</span><b>'+esc(m.current||'—')+'</b></div>'+
      '<div class="metric"><span>'+esc(t('newVersion'))+'</span><b>'+esc(m.latest||'—')+'</b></div>'+
    '</div>'+
    '<p class="muted">'+esc(t('updateReady'))+esc(size)+'</p>'+
    '<div class="detail-section"><h3>'+esc(t('updateContent'))+'</h3>'+renderUpdateNotes(m.notes||[])+'</div>'+
    '<div class="action-grid">'+
      '<a class="action-card" href="#" onclick="installUpdate();return false"><b>'+esc(t('downloadInstall'))+'</b><span>'+esc(t('installShort'))+'</span></a>'+
      '<a class="action-card" href="#" onclick="closeModal();return false"><b>'+esc(t('notNow'))+'</b><span>'+esc(t('keepCurrent'))+'</span></a>'+
    '</div>',
    'update-modal',
    {returnTo:returnTo}
  );
}
function showNoUpdate(m,checking,returnTo){
  var noUpdateText=m.update_configured===false
    ? t('noUpdateChannel')
    : (checking?t('checkingVersion'):t('alreadyLatest'));
  showModal(t('checkUpdate'),
    '<div class="metric-grid">'+
      '<div class="metric"><span>'+esc(t('currentVersion'))+'</span><b>'+esc(m.current||'—')+'</b></div>'+
      '<div class="metric"><span>'+esc(t('latestVersion'))+'</span><b>'+esc(m.latest||'—')+'</b></div>'+
    '</div>'+
    '<p class="muted">'+esc(noUpdateText)+'</p>'+
    '<div class="action-grid">'+
      (m.update_configured===false ? '' : '<a class="action-card" href="#" onclick="forceUpdateCheck();return false"><b>'+esc(t('recheck'))+'</b><span>'+esc(t('recheckDesc'))+'</span></a>')+
      '<a class="action-card" href="#" onclick="closeModal();return false"><b>'+esc(t('backSettings'))+'</b><span>'+esc(t('closePanel'))+'</span></a>'+
    '</div>',
    'update-modal',
    {returnTo:returnTo}
  );
}
function openPendingUpdate(){
  dismissUpdateToast();
  if(lastUpdateManifest&&lastUpdateManifest.has_update){
    showUpdateAvailable(lastUpdateManifest,null);
  }else{
    openUpdate();
  }
}
async function openUpdate(){
  showModal(t('checkUpdate'),'<div class="modal-loading">'+esc(t('readingUpdate'))+'</div>','update-modal',{returnTo:'settings'});
  try{
    var r=await fetch('/api/update/status');
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||t('updateStatusFailed'));
    var m=d.manifest||{};
    lastUpdateManifest=m;
    if(!m.has_update){
      showNoUpdate(m,!!d.checking,'settings');
      return;
    }
    showUpdateAvailable(m,'settings');
  }catch(e){
    showModal(t('checkUpdate'),'<div class="modal-error">'+esc(e.message||t('updateCheckFailed'))+'</div>','update-modal');
  }
}
async function forceUpdateCheck(){
  showModal(t('checkUpdate'),'<div class="modal-loading">'+esc(t('connectingUpdate'))+'</div>','update-modal',{returnTo:'settings'});
  try{
    var r=await fetch('/api/update/check',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||t('updateCheckFailed'));
    var m=d.manifest||{};
    lastUpdateManifest=m;
    if(m.has_update)showUpdateAvailable(m,'settings');
    else showNoUpdate(m,false,'settings');
  }catch(e){
    showModal(t('checkUpdate'),'<div class="modal-error">'+esc(e.message||t('updateCheckFailed'))+'</div>','update-modal',{returnTo:'settings'});
  }
}
async function checkUpdateSilently(){
  if(startupUpdateChecked)return;
  startupUpdateChecked=true;
  try{
    await loadSettingsQuietly(false);
    if(SETTINGS_STATE.auto_update_check===false)return;
    var r=await fetch('/api/update/status');
    var d=await r.json();
    var m=d.manifest||{};
    if(m.update_configured===false)return;
    if(d.ok&&m.has_update)showUpdateToast(m);
    else if(d.ok&&d.checking)setTimeout(function(){pollUpdateStatus(1)},5000);
  }catch(e){}
}
async function pollUpdateStatus(attempt){
  if(attempt>4)return;
  try{
    var r=await fetch('/api/update/status');
    var d=await r.json();
    var m=d.manifest||{};
    if(d.ok&&m.has_update)showUpdateToast(m);
    else if(d.ok&&d.checking)setTimeout(function(){pollUpdateStatus(attempt+1)},5000);
  }catch(e){}
}
async function installUpdate(){
  if(!lastUpdateManifest)return;
  showModal(t('installUpdate'),'<div class="modal-loading">'+esc(t('preparingInstall'))+'</div>','update-modal',{returnTo:null});
  try{
    var r=await fetch('/api/update/install',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({manifest:lastUpdateManifest})
    });
    var d=await r.json();
    if(!d.ok)throw new Error(d.error||t('installFailed'));
    showModal(t('installUpdate'),'<div class="modal-loading">'+esc(d.message||t('installingRestart'))+'</div>','update-modal',{returnTo:null});
  }catch(e){
    showModal(t('installUpdate'),'<div class="modal-error">'+esc(e.message||t('installFailed'))+'</div><div class="modal-loading">'+esc(t('installBackupNote'))+'</div>','update-modal',{returnTo:null});
  }
}
function renderAppDetail(d){
  var peak=d.peak_hour===null||d.peak_hour===undefined?'—':fmtHour(d.peak_hour);
  var density=d.response_density?Math.round(d.response_density)+t('perMin'):'—';
  var titles=d.top_titles&&d.top_titles.length?'<div class="detail-section"><h3>'+esc(t('windowTitles'))+'</h3><div class="data-table">'+d.top_titles.map(function(t){
    return '<div class="data-row"><b>'+fmtTime(t.seconds)+'</b><em title="'+esc(t.title)+'">'+esc(t.title)+'</em></div>';
  }).join('')+'</div></div>':'';
  var displayName=d.label?d.label:friendlyName(d.app);
  showModal(displayName,
    '<div class="detail-top">'+detailIconHtml(d)+'<div><div class="detail-name">'+esc(displayName)+'</div><div class="detail-sub">'+esc(appSubName(d.app))+' · '+fmtDateShort(d.date_start)+' '+esc(t('to'))+' '+fmtDateShort(d.date_end)+'</div></div></div>'+
    '<div class="metric-grid">'+
      '<div class="metric"><span>'+esc(t('totalTime'))+'</span><b>'+fmtTime(d.total_seconds)+'</b></div>'+
      '<div class="metric"><span>'+esc(t('todayMetric'))+'</span><b>'+fmtTime(d.today_seconds)+'</b></div>'+
      '<div class="metric"><span>'+esc(t('returns'))+'</span><b>'+fmtNum(d.session_count)+'</b></div>'+
      '<div class="metric"><span>'+esc(t('peak'))+'</span><b>'+peak+'</b></div>'+
      '<div class="metric"><span>'+esc(t('touchResponses'))+'</span><b>'+fmtNum(d.responses||0)+'</b></div>'+
      '<div class="metric"><span>'+esc(t('keystrokesMetric'))+'</span><b>'+fmtNum(d.keyboard||0)+'</b></div>'+
      '<div class="metric"><span>'+esc(t('mouseMetric'))+'</span><b>'+fmtNum(d.mouse||0)+'</b></div>'+
      '<div class="metric"><span>'+esc(t('density'))+'</span><b>'+density+'</b></div>'+
    '</div>'+
    '<div class="detail-section"><h3>'+esc(t('appTouchMap'))+'</h3>'+renderDetailHeat(d.heatmap||[])+'</div>'+
    '<div class="detail-section"><h3>'+esc(t('hourlyDistribution'))+'</h3>'+renderHourBars(d.hourly||[])+'</div>'+
    '<div class="detail-section"><h3>'+esc(t('dailyTime'))+'</h3>'+renderDayBars(d.daily||[])+'</div>'+
    titles
    ,'app-detail-modal'
  );
}
async function openAppDetail(app){
  if(hiddenApps().indexOf(String(app||''))>=0){
    showModal(friendlyName(app),'<div class="modal-error">'+esc(t('appHiddenNotice'))+'</div>','app-detail-modal');
    return;
  }
  showModal(friendlyName(app),'<div class="modal-loading">'+esc(t('appRecordLoading'))+'</div>','app-detail-modal');
  try{
    var r=await fetch('/api/app-analysis?days=7&app='+encodeURIComponent(app));
    renderAppDetail(await r.json());
  }catch(e){
    showModal(friendlyName(app),'<div class="modal-error">'+esc(t('appRecordFailed'))+'</div>','app-detail-modal');
  }
}
function renderInsights(d,opts){
  opts=opts||{};
  var findings=d.findings&&d.findings.length?d.findings:(d.judgments||[]);
  var en=getLang()==='en';
  var scope=d.scope||'week';
  var findingHtml=findings.length?findings.slice(0,3).map(function(x){
    return '<div class="insight-item clue"><span class="insight-badge"></span><span>'+esc(x)+'</span></div>';
  }).join(''):'<div class="insight-item">'+(en?'Not enough concentrated records yet.':'记录还不够集中，暂时无法选出稳定线索。')+'</div>';
  var actionHtml=scope==='day'
    ? '<div class="action-grid insight-actions"><a class="action-card" href="#" onclick="openAgentObservations();return false"><b>'+esc(t('agentWeeklyClues'))+'</b></a></div>'
    : '<div class="action-grid insight-actions"><a class="action-card" href="#" onclick="openMimoInsights();return false"><b>'+esc(t('dayReplay')+' · '+fmtDateLabel(selectedDate))+'</b><span>'+esc(t('dayReplayDesc'))+'</span></a></div>';
  var refreshIcon=opts.mimo
    ? '<button type="button" class="modal-icon-btn" onclick="refreshMimoInsights()" title="'+esc(t('aiRefresh'))+'" aria-label="'+esc(t('aiRefresh'))+'"><svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg></button>'
    : '';
  var cachedNote=opts.mimo&&opts.cached?'<div class="insight-foot">'+esc(t('aiCachedNote'))+'</div>':'';
  currentInsightRefs=d.references||d.theory_context||[];
  showModal(t('replay'),
    renderAiResponseBody(d,findingHtml)+
    actionHtml+
    cachedNote,
    'insight-modal',
    {returnTo:null,headerHtml:refreshIcon}
  );
}
function escJs(s){return String(s==null?'':s).replace(/\\/g,'\\\\').replace(/'/g,"\\'")}
var currentInsightRefs=[];
function renderResearchReferences(items){
  var references=Array.isArray(items)?items:[];
  if(!references.length)return '';
  return '<div class="insight-section"><h3>'+esc(t('aiReferences'))+'</h3><div class="research-list">'+references.slice(0,3).map(function(item){
    var title=item&&item.title?item.title:(item&&item.theory_id?item.theory_id:'');
    var level=item&&item.evidence_level?item.evidence_level:'';
    var theoryId=item&&item.theory_id?item.theory_id:'';
    var readLabel=t('aiReadFull');
    return '<button type="button" class="research-link" onclick="openReadingFullText(\''+escJs(theoryId)+'\')">'+
      '<span class="research-title">'+esc(title)+'</span>'+
      '<span class="research-meta"><b>'+esc(level)+'</b><i class="research-read">'+esc(readLabel)+' ›</i></span>'+
    '</button>';
  }).join('')+'</div></div>';
}
function openReadingFullText(theoryId){
  var lang=getLang()==='en'?'en':'zh';
  var meta=null;
  var refs=currentInsightRefs||[];
  for(var i=0;i<refs.length;i++){if(refs[i]&&refs[i].theory_id===theoryId){meta=refs[i];break}}
  var title=null;
  if(AGENT_READING_TITLES[theoryId]&&AGENT_READING_TITLES[theoryId][lang])title=AGENT_READING_TITLES[theoryId][lang];
  if(!title&&meta&&meta.title)title=meta.title;
  if(!title)title=theoryId;
  var ft=AGENT_READING_FULLTEXT[theoryId];
  if(!ft){
    showModal(title,'<div class="modal-error">'+esc(t('aiNoFullText'))+'</div>','insight-modal',{returnTo:null});
    return;
  }
  var paras=(lang==='en'?(ft.en||ft.zh):(ft.zh||ft.en))||[];
  var metaHtml='';
  if(meta){
    var bits=[];
    if(meta.evidence_level)bits.push(esc(t('aiEvidence'))+': '+esc(meta.evidence_level));
    if(meta.original_domain)bits.push(esc(meta.original_domain));
    if(meta.source_url)bits.push('<a href="'+esc(meta.source_url)+'" target="_blank" rel="noopener noreferrer">DOI</a>');
    metaHtml='<div class="reading-meta">'+bits.join(' · ')+'</div>';
  }
  var html='<div class="reading-fulltext">'+metaHtml+paras.map(function(p){
    if(!p)return '';
    if(p.h)return '<h4>'+esc(p.h)+'</h4>'+(p.b?'<p>'+esc(p.b)+'</p>':'');
    return '<p>'+esc(p)+'</p>';
  }).join('')+'</div>';
  showModal(title,html,'insight-modal',{returnTo:null});
}
function renderAiTextSection(title,items,kind){
  var values=Array.isArray(items)?items.filter(Boolean):[];
  if(!values.length)return '';
  return '<div class="insight-section"><h3>'+esc(title)+'</h3><div class="insight-list">'+values.map(function(item,index){
    return '<div class="insight-item '+esc(kind||'')+'"><span class="insight-badge">'+(kind==='suggestion'?esc(String(index+1)):'')+'</span><span>'+esc(item)+'</span></div>';
  }).join('')+'</div></div>';
}
function renderAiResponseBody(d,findingHtml){
  var en=getLang()==='en';
  return '<div class="insight-list"><div class="insight-item lead">'+esc(d.conclusion||(en?'Not enough records yet.':'当前记录还不足以形成稳定结论。'))+'</div></div>'+
    (findingHtml?'<div class="insight-section"><h3>'+esc(t('aiFindings'))+'</h3><div class="insight-list">'+findingHtml+'</div></div>':'')+
    renderAiTextSection(t('aiQuestions'),d.reflection_questions,'clue')+
    renderAiTextSection(t('aiSuggestions'),d.suggestions,'suggestion')+
    renderResearchReferences(d.references||d.theory_context);
}
var currentReadingIndex=0;
var currentReadingBatch=[];
function sampleReadingBatch(){
  var pool=(AGENT_READING_PARAGRAPHS||[]).slice();
  for(var i=pool.length-1;i>0;i--){var j=Math.floor(Math.random()*(i+1));var tmp=pool[i];pool[i]=pool[j];pool[j]=tmp;}
  currentReadingBatch=pool.slice(0,3);
  currentReadingIndex=0;
}
function agentReadingInner(){
  var lang=getLang()==='en'?'en':'zh';
  if(!currentReadingBatch||!currentReadingBatch.length)sampleReadingBatch();
  if(currentReadingIndex>=currentReadingBatch.length)currentReadingIndex=0;
  if(currentReadingIndex<0)currentReadingIndex=currentReadingBatch.length-1;
  var item=currentReadingBatch[currentReadingIndex];
  var title=(AGENT_READING_TITLES[item.id]&&AGENT_READING_TITLES[item.id][lang])||item.id;
  var prevLabel=lang==='en'?'Previous':'上一条';
  var nextLabel=lang==='en'?'Next':'下一条';
  var shuffleLabel=lang==='en'?'Next batch':'换一批';
  return '<div class="agent-theory-item"><b>'+esc(title)+'</b><span>'+esc(item[lang]||item.zh||'')+'</span></div>'+
    '<div class="agent-theory-nav">'+
      '<button type="button" onclick="readingNav(-1)" title="'+prevLabel+'" aria-label="'+prevLabel+'">‹</button>'+
      '<span>'+(currentReadingIndex+1)+' / '+currentReadingBatch.length+'</span>'+
      '<button type="button" onclick="readingNav(1)" title="'+nextLabel+'" aria-label="'+nextLabel+'">›</button>'+
      '<button type="button" onclick="sampleReadingBatch();readingNav(0)" title="'+shuffleLabel+'" aria-label="'+shuffleLabel+'">↻</button>'+
    '</div>';
}
function agentReadingHtml(){
  currentReadingBatch=[];
  return '<div class="analysis-reading" id="agent-reading-box">'+agentReadingInner()+'</div>';
}
function readingNav(delta){
  var batch=currentReadingBatch||[];
  if(!batch.length)sampleReadingBatch();
  batch=currentReadingBatch;
  if(delta>0&&currentReadingIndex>=batch.length-1){sampleReadingBatch();}
  else if(delta!==0){currentReadingIndex=(currentReadingIndex+delta+batch.length)%batch.length;}
  var box=document.getElementById('agent-reading-box');
  if(box)box.innerHTML=agentReadingInner();
}
function mimoAnalyzingHtml(){
  return '<div class="analysis-panel" role="status" aria-live="polite">'+
    '<div class="analysis-hero"><div class="analysis-orb">AI</div><div><b>'+esc(t('aiAnalyzingTitle'))+'</b><span>'+esc(t('aiAnalyzingHint'))+'</span></div></div>'+
    '<div class="analysis-bar"><span></span></div>'+
    agentReadingHtml()+
  '</div>';
}
function waitMs(ms){return new Promise(function(resolve){setTimeout(resolve,ms)})}
var AI_REPLAY_TTL_MS=12*3600*1000;
var insightLive=false;
function aiReplayCacheKey(date){return 'kx-ai-replay:'+date}
function writeAiReplayCache(date,data){
  try{localStorage.setItem(aiReplayCacheKey(date),JSON.stringify({at:Date.now(),data:data}))}catch(e){}
}
function readAiReplayCache(date){
  try{
    var raw=localStorage.getItem(aiReplayCacheKey(date));
    if(!raw)return null;
    var box=JSON.parse(raw);
    if(!box||!box.data||!box.at)return null;
    if(Date.now()-Number(box.at)>=AI_REPLAY_TTL_MS)return null;
    return box.data;
  }catch(e){return null}
}
function clearAiReplayCache(date){
  try{localStorage.removeItem(aiReplayCacheKey(date))}catch(e){}
}
async function refreshMimoInsights(){
  clearAiReplayCache(selectedDate);
  await openMimoInsights({force:true});
}
async function openMimoInsights(options){
  options=options||{};
  if(!await ensureAiKeyReady()){showAiSetupGuide(null);return}
  var started=Date.now();
  insightLive=true;
  showModal(t('aiAnalyzingTitle'),mimoAnalyzingHtml(),'analysis-modal',{returnTo:null});
  if(!options.force){
    var cached=readAiReplayCache(selectedDate);
    if(cached&&cached.ok!==false){
      if(!insightLive)return;
      renderInsights(cached,{mimo:true,cached:true});
      return;
    }
  }
  async function localFallback(){
    var localResponse=await fetch('/api/day-replay?date='+encodeURIComponent(selectedDate));
    var localData=await localResponse.json();
    if(!localResponse.ok||!localData)throw new Error(t('replayFailed'));
    localData.model_note=t('aiFastFallback');
    if(!insightLive)return;
    renderInsights(localData,{mimo:true});
  }
  try{
    var r=await fetch('/api/replay/jobs',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({date:selectedDate})
    });
    var submitted=await r.json();
    if(r.ok&&submitted.ok!==false){
      var d=null;
      var deadline=started+90000;
      for(var attempt=0;attempt<240;attempt++){
        var remaining=deadline-Date.now();
        if(remaining<=0)break;
        await waitMs(Math.min(500,remaining));
        if(Date.now()>=deadline)break;
        if(!insightLive)return;
        var statusResponse=await fetch('/api/replay/jobs?id='+encodeURIComponent(submitted.job_id));
        var status=await statusResponse.json();
        if(statusResponse.ok&&status.ok!==false&&status.status==='completed'){
          d=status.result;
          break;
        }
      }
      if(d){
        if(!insightLive)return;
        writeAiReplayCache(selectedDate,d);
        renderInsights(d,{mimo:true});
        return;
      }
    }
    await localFallback();
  }catch(e){
    try{
      await localFallback();
    }catch(e2){
      if(!insightLive)return;
      var msg=(e2&&e2.message)?e2.message:t('replayFailed');
      showModal(t('replay'),'<div class="modal-error">'+esc(t('replayFailed'))+'：'+esc(msg)+'</div>','insight-modal',{returnTo:null});
    }
  }
}
async function openInsights(){
  showModal(t('replay'),'<div class="modal-loading">'+esc(t('dayReplayLoading'))+'</div>','insight-modal',{returnTo:null});
  try{
    var r=await fetch('/api/day-replay?date='+encodeURIComponent(selectedDate));
    if(!r.ok)throw new Error('replay failed');
    renderInsights(await r.json());
  }catch(e){
    showModal(t('replay'),'<div class="modal-error">'+esc(t('replayFailed'))+'</div>','insight-modal',{returnTo:null});
  }
}
async function openWeeklyInsights(){
  showModal(t('replay'),'<div class="modal-loading">'+esc(t('weekReplayLoading'))+'</div>','insight-modal',{returnTo:null});
  try{
    var r=await fetch('/api/insights?days=7');
    if(!r.ok)throw new Error('replay failed');
    var d=await r.json();
    d.scope=d.scope||'week';
    renderInsights(d);
  }catch(e){
    showModal(t('replay'),'<div class="modal-error">'+esc(t('replayFailed'))+'</div>','insight-modal',{returnTo:null});
  }
}

/* ── Render stats ──────────────── */
function render(d){
  var keyboard=d.keystrokes||0,mouse=d.mouse_events||0,responses=d.responses||keyboard+mouse;
  document.getElementById('v-keys').textContent=fmtNum(keyboard);
  if(d.mouse_split_available){
    document.getElementById('v-clicks').textContent=fmtNum(d.mouse_button_events||0);
    document.getElementById('v-scroll').textContent=fmtNum(d.scroll_events||0);
  }else{
    document.getElementById('v-clicks').textContent=fmtNum(mouse);
    document.getElementById('v-scroll').textContent='—';
  }
  document.getElementById('v-time').textContent=fmtTime(d.total_seconds||0);
  var dateEl=document.getElementById('today-date');
  var scoreEl=document.getElementById('today-score');
  if(dateEl)updateDateControls();
  if(scoreEl)scoreEl.textContent=fmtNum(responses||0);
}

/* ── Heatmap (previous 5 days + today) ─ */
function heatColor(v,mx){
  if(v===0)return 'var(--heat-empty)';
  var t=Math.pow(v/mx,.5);
  if(t<.16)return 'var(--heat-1)';
  if(t<.32)return 'var(--heat-2)';
  if(t<.48)return 'var(--heat-3)';
  if(t<.64)return 'var(--heat-4)';
  if(t<.82)return 'var(--heat-5)';
  return 'var(--heat-6)';
}
var WEEKDAYS_ZH=['周日','周一','周二','周三','周四','周五','周六'];
var WEEKDAYS_EN=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
var heatmapDays=[];
var heatRange='week';
var monthHeatmap=null;
var yearHeatmap=null;
var heatMonthCursor=selectedDate.slice(0,7);
var heatYearCursor=selectedDate.slice(0,4);

function resetHeatPeriodCursors(){
  heatMonthCursor=selectedDate.slice(0,7);
  heatYearCursor=selectedDate.slice(0,4);
}
function shiftMonthIso(month,delta){
  var p=String(month||'').split('-');
  var dt=new Date(Number(p[0]),Number(p[1]||1)-1+delta,1);
  return dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0');
}
function updateHeatPeriodControls(){
  var nav=document.getElementById('heat-period-nav');
  var prev=document.getElementById('heat-period-prev');
  var next=document.getElementById('heat-period-next');
  var visible=heatRange==='month'||heatRange==='year';
  if(nav)nav.classList.toggle('visible',visible);
  if(!visible)return;
  var isMonth=heatRange==='month';
  var cursor=isMonth?heatMonthCursor:heatYearCursor;
  var current=isMonth?todayIso().slice(0,7):todayIso().slice(0,4);
  if(prev){
    prev.disabled=!isMonth&&Number(cursor)<=2000;
    prev.setAttribute('title',t(isMonth?'previousMonth':'previousYear'));
    prev.setAttribute('aria-label',t(isMonth?'previousMonth':'previousYear'));
  }
  if(next){
    next.disabled=cursor>=current;
    next.setAttribute('title',t(isMonth?'nextMonth':'nextYear'));
    next.setAttribute('aria-label',t(isMonth?'nextMonth':'nextYear'));
  }
}
function changeHeatPeriod(delta){
  if(heatRange==='month'){
    var nextMonth=shiftMonthIso(heatMonthCursor,delta);
    if(nextMonth>todayIso().slice(0,7))return;
    heatMonthCursor=nextMonth;
    monthHeatmap=null;
    updateHeatPeriodControls();
    loadMonthHeatmap();
  }else if(heatRange==='year'){
    var nextYear=String(Number(heatYearCursor)+delta);
    if(Number(nextYear)<2000||nextYear>todayIso().slice(0,4))return;
    heatYearCursor=nextYear;
    yearHeatmap=null;
    updateHeatPeriodControls();
    loadYearHeatmap();
  }
}
function jumpToHeatDate(date){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(String(date||''))||date>todayIso())return;
  userPinnedDate=true;
  selectedDate=date;
  lastAppsLayoutSignature='';
  heatMonthCursor=date.slice(0,7);
  heatYearCursor=date.slice(0,4);
  updateDateControls();
  updateHeatPeriodControls();
  loadData();
}
function drillIntoMonth(month){
  if(!/^\d{4}-\d{2}$/.test(String(month||''))||month>todayIso().slice(0,7))return;
  heatMonthCursor=month;
  monthHeatmap=null;
  setHeatRange('month');
}

function weekdayLabel(date){
  var dt=new Date(date+'T00:00:00');
  if(isNaN(dt.getTime()))return '';
  var labels=getLang()==='en'?WEEKDAYS_EN:WEEKDAYS_ZH;
  return labels[dt.getDay()]||'';
}

function selectedHeatValue(day,h){
  var includeKeyboard=document.getElementById('heat-keyboard')?.checked!==false;
  var includeMouse=document.getElementById('heat-mouse')?.checked!==false;
  var k=(day.keyboard_hourly&&day.keyboard_hourly[h])||0;
  var m=(day.mouse_hourly&&day.mouse_hourly[h])||0;
  return (includeKeyboard?k:0)+(includeMouse?m:0);
}
function selectedDayValue(day){
  var includeKeyboard=document.getElementById('heat-keyboard')?.checked!==false;
  var includeMouse=document.getElementById('heat-mouse')?.checked!==false;
  return (includeKeyboard?(day.keyboard||0):0)+(includeMouse?(day.mouse||0):0);
}
function setHeatRange(range){
  heatRange=range==='year'?'year':(range==='month'?'month':'week');
  document.getElementById('heat-week-btn')?.classList.toggle('active',heatRange==='week');
  document.getElementById('heat-month-btn')?.classList.toggle('active',heatRange==='month');
  document.getElementById('heat-year-btn')?.classList.toggle('active',heatRange==='year');
  updateHeatPeriodControls();
  if(heatRange==='year'&&(!yearHeatmap||yearHeatmap.year!==heatYearCursor))loadYearHeatmap();
  else if(heatRange==='month'&&(!monthHeatmap||monthHeatmap.month!==heatMonthCursor))loadMonthHeatmap();
  else renderHeatmap();
}
function renderHeatmap(){
  try{
    if(heatRange==='year')return renderYearHeatmap();
    if(heatRange==='month')return renderMonthHeatmap();
    var days=heatmapDays||[];
    var mx=1;
    for(var d=0;d<days.length;d++)for(var h=0;h<24;h++){var sv=selectedHeatValue(days[d],h);if(sv>mx)mx=sv;}
    var grid=document.getElementById('heat-grid'),hourLbl=document.getElementById('heat-day-labels'),dayLbl=document.getElementById('heat-hours-labels'),weekLbl=document.getElementById('heat-weekday-labels');
    grid.classList.remove('month','year');
    var dayCount=Math.max(1,days.length||6);
    grid.style.setProperty('--heat-day-count',dayCount);
    dayLbl.style.setProperty('--heat-day-count',dayCount);
    if(weekLbl)weekLbl.style.setProperty('--heat-day-count',dayCount);
    var now=new Date(),today=todayIso(),nowHour=now.getHours();
    var nowText=document.getElementById('heat-now-text');
    if(nowText)nowText.innerHTML='<span class="recording-dot" aria-hidden="true"></span>'+esc(t('recordingNow'))+' · '+fmtHourRange(nowHour);
    hourLbl.innerHTML='';
    dayLbl.innerHTML=days.map(function(d,i){
      var dt=new Date(d.date+'T00:00:00');
      return '<span>'+(dt.getMonth()+1)+'/'+dt.getDate()+'</span>';
    }).join('');
    if(weekLbl){
      weekLbl.innerHTML=days.map(function(d){return '<span>'+esc(weekdayLabel(d.date))+'</span>';}).join('');
    }
    var cells='';
    for(var c=0;c<days.length;c++){
      cells+='<div class="day-block">';
      for(var h=0;h<24;h++){
        var dt=new Date(days[c].date+'T00:00:00');
        var v=selectedHeatValue(days[c],h);
        var k=(days[c].keyboard_hourly&&days[c].keyboard_hourly[h])||0;
        var m=(days[c].mouse_hourly&&days[c].mouse_hourly[h])||0;
        var label=fmtHourRange(h)+' · '+t('keyboardShort')+' '+fmtNum(k)+' · '+t('mouseShort')+' '+fmtNum(m);
        var isNow=days[c].date===today&&h===nowHour;
        cells+='<div class="hc'+(isNow?' now':'')+'" style="background:'+heatColor(v,mx)+'" data-date="'+esc(days[c].date)+'" data-hour="'+h+'" data-tip="'+tipAttr(label)+'"></div>';
      }
      cells+='</div>';
    }
    grid.innerHTML=cells;
    grid.querySelectorAll('.hc[data-tip]').forEach(function(cell){
      cell.addEventListener('mouseenter',function(){
        if(nowText)nowText.textContent=cell.dataset.tip||fmtHourRange(nowHour);
      });
      cell.addEventListener('mouseleave',function(){
        if(nowText)nowText.textContent=fmtHourRange(nowHour);
      });
      cell.addEventListener('click',function(){
        openHourDetail(cell.dataset.date,Number(cell.dataset.hour||0));
      });
    });
    paintHeatLegend(mx);
  }catch(e){}
}
function renderMonthHeatmap(){
  try{
    var days=(monthHeatmap&&monthHeatmap.days)||[];
    var grid=document.getElementById('heat-grid'),dayLbl=document.getElementById('heat-hours-labels'),weekLbl=document.getElementById('heat-weekday-labels'),nowText=document.getElementById('heat-now-text');
    var mx=1;
    days.forEach(function(d){var v=selectedDayValue(d);if(v>mx)mx=v;});
    grid.classList.remove('year');
    grid.classList.add('month');
    grid.style.removeProperty('--heat-day-count');
    dayLbl.innerHTML='';
    if(weekLbl)weekLbl.innerHTML='';
    if(nowText)nowText.innerHTML=monthHeatmap?'<span class="heat-period-label">'+esc(monthHeatmap.month)+'</span>':'';
    grid.innerHTML=days.map(function(d){
      var label=fmtDateLabel(d.date)+' · '+t('keyboardShort')+' '+fmtNum(d.keyboard||0)+' · '+t('mouseShort')+' '+fmtNum(d.mouse||0);
      var future=d.date>todayIso();
      return '<div class="month-cell'+(d.date===selectedDate?' selected':'')+(future?' future':'')+'" style="background:'+heatColor(selectedDayValue(d),mx)+'" data-date="'+esc(d.date)+'" data-tip="'+tipAttr(label)+'" title="'+esc(label)+'"></div>';
    }).join('');
    grid.querySelectorAll('.month-cell[data-tip]').forEach(function(cell){
      cell.addEventListener('mouseenter',function(){if(nowText)nowText.textContent=cell.dataset.tip||''});
      cell.addEventListener('mouseleave',function(){if(nowText)nowText.textContent=monthHeatmap?monthHeatmap.month:''});
      cell.addEventListener('click',function(){jumpToHeatDate(cell.dataset.date||'')});
    });
    updateHeatPeriodControls();
    paintHeatLegend(mx);
  }catch(e){}
}
function monthLabel(month){
  var p=String(month||'').split('-');
  if(p.length!==2)return month||'';
  var n=Number(p[1]);
  var names=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return getLang()==='en'?(names[n-1]||String(n)):(n+'月');
}
function renderYearHeatmap(){
  try{
    var months=(yearHeatmap&&yearHeatmap.months)||[];
    var grid=document.getElementById('heat-grid'),dayLbl=document.getElementById('heat-hours-labels'),weekLbl=document.getElementById('heat-weekday-labels'),nowText=document.getElementById('heat-now-text');
    var mx=1;
    months.forEach(function(month){(month.days||[]).forEach(function(d){var v=selectedDayValue(d);if(v>mx)mx=v;})});
    grid.classList.remove('month');
    grid.classList.add('year');
    grid.style.removeProperty('--heat-day-count');
    dayLbl.innerHTML='';
    if(weekLbl)weekLbl.innerHTML='';
    if(nowText)nowText.innerHTML=yearHeatmap?'<span class="heat-period-label">'+esc(yearHeatmap.year)+'</span> <span class="heat-drill-hint">'+esc(t('yearDrillHint'))+'</span>':'';
    grid.innerHTML=months.map(function(month){
      var cells=(month.days||[]).map(function(d){
        var label=fmtDateLabel(d.date)+' · '+t('keyboardShort')+' '+fmtNum(d.keyboard||0)+' · '+t('mouseShort')+' '+fmtNum(d.mouse||0);
        var future=d.date>todayIso();
        return '<div class="year-cell'+(d.date===selectedDate?' selected':'')+(future?' future':'')+'" style="background:'+heatColor(selectedDayValue(d),mx)+'" data-date="'+esc(d.date)+'" data-tip="'+tipAttr(label)+'" title="'+esc(label)+'"></div>';
      }).join('');
      var futureMonth=month.month>todayIso().slice(0,7);
      return '<div class="year-month"><div class="year-month-grid">'+cells+'</div><button type="button" class="year-month-label" data-month="'+esc(month.month)+'"'+(futureMonth?' disabled':'')+'>'+esc(monthLabel(month.month))+'</button></div>';
    }).join('');
    grid.querySelectorAll('.year-cell[data-tip]').forEach(function(cell){
      cell.addEventListener('mouseenter',function(){if(nowText)nowText.textContent=cell.dataset.tip||''});
      cell.addEventListener('mouseleave',function(){if(nowText)nowText.innerHTML=yearHeatmap?'<span class="heat-period-label">'+esc(yearHeatmap.year)+'</span> <span class="heat-drill-hint">'+esc(t('yearDrillHint'))+'</span>':''});
      cell.addEventListener('click',function(){jumpToHeatDate(cell.dataset.date||'')});
    });
    grid.querySelectorAll('.year-month-label[data-month]').forEach(function(button){
      button.addEventListener('click',function(){drillIntoMonth(button.dataset.month||'')});
    });
    updateHeatPeriodControls();
    paintHeatLegend(mx);
  }catch(e){}
}
function paintHeatLegend(mx){
  var lg=document.getElementById('heat-lg');
  if(!lg)return;
  var is=lg.querySelectorAll('i');
  for(var i=0;i<is.length;i++){
    var ratio=i===0?0:Math.pow(i/(is.length-1),1.35);
    is[i].style.background=heatColor(mx*ratio,mx);
  }
}
async function openHourDetail(date,hour){
  showModal(t('hourDetail'),'<div class="modal-loading">'+esc(t('readingStrength'))+'</div>');
  try{
    var r=await fetch('/api/hour-detail?date='+encodeURIComponent(date)+'&hour='+encodeURIComponent(hour));
    var d=await r.json();
    var keyUnit=getLang()==='en'?' keys':'键';
    var mouseUnit=getLang()==='en'?' mouse':'鼠';
    var apps=d.apps&&d.apps.length?d.apps.map(function(a){
      var share=a.share?Math.round(a.share*100):0;
      return '<div class="data-row"><b>'+fmtNum(a.responses||0)+'</b><em>'+esc(friendlyName(a.app))+' · '+fmtNum(a.keyboard||0)+keyUnit+' / '+fmtNum(a.mouse||0)+mouseUnit+' · '+share+'%</em></div>';
    }).join(''):'<div class="empty">'+esc(t('noHourApps'))+'</div>';
    showModal(
      t('hourDetail'),
      '<div class="detail-top"><div><div class="detail-name">'+esc(fmtDateLabel(d.date)+' '+fmtHourRange(d.hour))+'</div><div class="detail-sub">'+esc(t('keyboardShort'))+' '+fmtNum(d.keyboard||0)+' · '+esc(t('mouseShort'))+' '+fmtNum(d.mouse||0)+'</div></div></div>'+
      '<div class="metric-grid">'+
        '<div class="metric"><span>'+esc(t('touchResponses'))+'</span><b>'+fmtNum(d.responses||0)+'</b></div>'+
        '<div class="metric"><span>'+esc(t('keystrokesMetric'))+'</span><b>'+fmtNum(d.keyboard||0)+'</b></div>'+
        '<div class="metric"><span>'+esc(t('mouseMetric'))+'</span><b>'+fmtNum(d.mouse||0)+'</b></div>'+
        '<div class="metric"><span>'+esc(t('hourApps'))+'</span><b>'+fmtNum((d.apps||[]).length)+'</b></div>'+
      '</div>'+
      '<div class="detail-section"><h3>'+esc(t('hourApps'))+'</h3><div class="data-table">'+apps+'</div></div>'
    );
  }catch(e){
    showModal(t('hourDetail'),'<div class="modal-error">'+esc(t('appRecordFailed'))+'</div>');
  }
}
async function loadHeatmap(){
  try{
    var r=await fetch('/api/heatmap?days=6&end='+encodeURIComponent(selectedDate)),j=await r.json();
    heatmapDays=j.data||[];
    document.getElementById('heat-grid')?.classList.remove('month');
    renderHeatmap();
  }catch(e){}
}
async function loadMonthHeatmap(){
  try{
    var month=heatMonthCursor;
    var r=await fetch('/api/month-heatmap?month='+encodeURIComponent(month)),j=await r.json();
    monthHeatmap=j;
    renderMonthHeatmap();
  }catch(e){}
}
async function loadYearHeatmap(){
  try{
    var year=heatYearCursor;
    var r=await fetch('/api/year-heatmap?year='+encodeURIComponent(year)),j=await r.json();
    yearHeatmap=j;
    renderYearHeatmap();
  }catch(e){}
}

/* ── App bars ──────────────────── */
var lastAppsLayoutSignature='';
function renderApps(apps){
  try{
    apps=apps||[];
    apps=apps.filter(appVisible);
    agentAppsCount=apps.length;
    if(!apps.length){document.getElementById('app-list').innerHTML='<div class="empty">'+t(selectedDate===todayIso()?'waitingToday':'noData')+'</div>';document.getElementById('app-count').textContent='';syncAgentChrome();return;}
    var maxResponses=apps.reduce(function(a,b){return Math.max(a,b.responses||0)},0);
    var maxSeconds=apps.reduce(function(a,b){return Math.max(a,b.seconds||0)},0);
    var totalResponses=apps.reduce(function(a,b){return a+(b.responses||0)},0);
    var totalSeconds=apps.reduce(function(a,b){return a+(b.seconds||0)},0);
    var topApps=apps.slice(0,8);
    document.getElementById('app-count').textContent='';
    var signature=topApps.map(function(a){return [a.app,a.path||'',a.label||''].join('|')}).join('||');
    if(signature!==lastAppsLayoutSignature){
      lastAppsLayoutSignature=signature;
      document.getElementById('app-list').innerHTML=topApps.map(function(a,i){
        return '<div class="app-row" data-app="'+esc(encodeURIComponent(a.app))+'"><div class="app-rank '+(RANKS[i]||'rx')+'">'+(i+1)+'</div>'+iconHtml(a)+'<div class="app-name" title="'+esc(friendlyName(a.app))+'">'+esc(appDisplayName(a))+'</div><div class="app-meta"></div><div class="app-track"><div class="app-fill '+FILLS[i%FILLS.length]+'"></div></div><button type="button" class="app-label-btn" data-app="'+esc(encodeURIComponent(a.app))+'" title="'+esc(t('appLabelEdit'))+'" aria-label="'+esc(t('appLabelEdit'))+'">✎</button></div>';
      }).join('');
      document.querySelectorAll('.app-row[data-app]').forEach(function(row){
        row.addEventListener('click',function(){openAppDetail(decodeURIComponent(row.dataset.app))});
      });
      document.querySelectorAll('.app-label-btn[data-app]').forEach(function(btn){
        btn.addEventListener('click',function(e){
          e.stopPropagation();
          var target=decodeURIComponent(btn.dataset.app);
          var item=apps.filter(function(a){return a.app===target})[0]||{};
          editAppLabel(target,item.label||'');
        });
      });
    }
    var rows=document.querySelectorAll('.app-row[data-app]');
    topApps.forEach(function(a,i){
      var row=rows[i];
      if(!row)return;
      var responses=a.responses||0,seconds=a.seconds||0;
      var pct=maxResponses>0?(responses/maxResponses*100):(maxSeconds>0?(seconds/maxSeconds*100):0);
      var durationShare=totalSeconds>0?(seconds/totalSeconds*100):0;
      var fill=row.querySelector('.app-fill'),meta=row.querySelector('.app-meta');
      if(fill)fill.style.width=pct.toFixed(1)+'%';
      if(meta){
        meta.textContent=fmtTime(seconds)+' · '+durationShare.toFixed(0)+'%';
      }
    });
    syncAgentChrome();
  }catch(e){}
}
async function loadApps(){
  try{
    var r=await fetch('/api/apps?date='+encodeURIComponent(selectedDate)),j=await r.json();
    renderApps(j.apps||[]);
  }catch(e){}
}

/* ── 下拉刷新 ─────────────────── */
let touchStartY = 0;
let touchEndY = 0;
let isRefreshing = false;

document.querySelector('.wrap')?.addEventListener('touchstart', (e) => {
  touchStartY = e.touches[0].clientY;
}, { passive: true });

document.querySelector('.wrap')?.addEventListener('touchmove', (e) => {
  touchEndY = e.touches[0].clientY;
  const pullDistance = touchEndY - touchStartY;

  // 只在页面顶部时触发下拉刷新
  if (pullDistance > 50 && window.scrollY <= 0 && !isRefreshing) {
    document.getElementById('pull-to-refresh').classList.add('active');
  }
}, { passive: true });

document.querySelector('.wrap')?.addEventListener('touchend', async () => {
  const pullDistance = touchEndY - touchStartY;

  if (pullDistance > 50 && window.scrollY <= 0 && !isRefreshing) {
    isRefreshing = true;
    await loadData();
    document.getElementById('pull-to-refresh').classList.remove('active');
    isRefreshing = false;
  }

  touchStartY = 0;
  touchEndY = 0;
}, { passive: true });

/* ── 数据加载 ─────────────────── */
async function fetchJson(url){
  var r=await fetch(url);
  if(!r.ok)throw new Error('HTTP '+r.status);
  return r.json();
}
async function loadData() {
  setOfflineMessage(false);
  updateDateControls();
  var agentPanel=document.getElementById('agent-panel');
  if(agentPanel&&agentView!=='apps'&&!(agentObservationFeed&&agentObservationFeed.ok)){
    agentObservationFeed=null;
    syncAgentChrome();
    agentPanel.innerHTML='<div class="agent-empty">'+esc(t('agentObservationLoading'))+'</div>';
    agentPanel.setAttribute('data-rendered','0');
  }
  var urls=[
    '/api/day?date='+encodeURIComponent(selectedDate),
    '/api/heatmap?days=6&end='+encodeURIComponent(selectedDate),
    '/api/apps?date='+encodeURIComponent(selectedDate),
    '/api/agent/observations?date='+encodeURIComponent(selectedDate)+'&purpose='+encodeURIComponent(agentPurposeId)
  ];
  var results=await Promise.allSettled(urls.map(fetchJson));
  var ok=false;
  if(results[0].status==='fulfilled'){
    render(results[0].value);
    ok=true;
  }
  if(results[1].status==='fulfilled'){
    heatmapDays=results[1].value.data||[];
    if(heatRange==='year'&&!yearHeatmap)loadYearHeatmap();
    else if(heatRange==='month'&&!monthHeatmap)loadMonthHeatmap();
    else renderHeatmap();
    ok=true;
  }
  if(results[2].status==='fulfilled'){
    renderApps(results[2].value.apps||[]);
    ok=true;
  }
  if(results[3].status==='fulfilled'){
    renderAgentObservations(results[3].value);
    ok=true;
  }else if(agentView!=='apps'){
    var reason=results[3].reason;
    var message=reason&&reason.message?reason.message:t('agentObservationLoading');
    renderAgentObservations({ok:false,error:message});
  }
  if(!ok){
    setOfflineMessage(true,t('connecting'));
    if(!dataRetryTimer){
      dataRetryTimer=setTimeout(function(){
        dataRetryTimer=null;
        loadData();
      },1200);
    }
  }else if(dataRetryTimer){
    clearTimeout(dataRetryTimer);
    dataRetryTimer=null;
  }
}

/* ── Init ──────────────────────── */
// 视觉检查钩子：/?autostep=fn1;fn2 会在 init 完成后依次执行，仅用于
// headless 截图与本地视觉回归，正常用户不会携带该参数。
function runAutostep(){
  var raw=new URLSearchParams(location.search).get('autostep');
  if(!raw)return;
  raw.split(';').forEach(function(expr){
    if(!expr)return;
    try{ (new Function('return ('+expr+')'))(); }
    catch(e){ console.error('autostep failed:', expr, e); }
  });
}

async function init(){
  try{
    selectedDate=todayIso();
    userPinnedDate=false;
    lastKnownToday=todayIso();
    document.getElementById('date-prev')?.addEventListener('click',function(){changeDate(-1)});
    document.getElementById('date-next')?.addEventListener('click',function(){changeDate(1)});
    updateDateControls();
    loadAgentPurposes();
    await loadData();
    var firstRun=maybeShowIntro();
    if(firstRun){aiGuidePending=true;}
    runAutostep();
    setTimeout(checkUpdateSilently, 2500);

    // 定时刷新（降低频率以节省电量）
    setInterval(async function(){
      var nowToday=todayIso();
      if(nowToday!==lastKnownToday){
        lastKnownToday=nowToday;
        if(!userPinnedDate){
          selectedDate=nowToday;
          updateDateControls();
          loadData();
        }
      }
      if(document.hidden||!isSelectedToday())return;
      try{
        var r=await fetch('/api/today');
        render(await r.json());
      }catch(e){}
    }, 3500);

    setInterval(function(){
      if(document.hidden)return;
      if(isSelectedToday()){
        loadHeatmap();
        loadApps();
        loadAgentObservations({silent:true});
      }
    }, 60000);

  }catch(e){
    setOfflineMessage(true,t('connecting'));
  }
}

function introHtml(){
  return '<div class="intro-title">'+esc(t('introTitle'))+'</div>'+
    '<p class="intro-copy">'+esc(t('introBody'))+'</p>'+
    '<div class="intro-points">'+
      '<div class="intro-point">'+esc(t('intro1'))+'</div>'+
      '<div class="intro-point">'+esc(t('intro2'))+'</div>'+
      '<div class="intro-point">'+esc(t('intro3'))+'</div>'+
      '<div class="intro-point">'+esc(t('intro4'))+'</div>'+
    '</div>'+
    '<div class="action-grid">'+
      '<a class="action-card" href="#" onclick="markIntroSeen();hideModal(function(){if(aiGuidePending)showAiConfigGuideIfFirstRun()});return false"><b>'+esc(t('start'))+'</b><span>'+esc(t('subtitle'))+'</span></a>'+
      '<a class="action-card" href="#" onclick="toggleLang();document.getElementById(&quot;modal-title&quot;).textContent=t(&quot;introTitle&quot;);document.getElementById(&quot;modal-body&quot;).innerHTML=introHtml();return false"><b>'+esc(t('langToggle'))+'</b><span>中文 / English</span></a>'+
    '</div>';
}
function markIntroSeen(){
  var key='kx-intro-v0.10.1';
  localStorage.setItem(key,'1');
  SETTINGS_STATE.intro_seen=true;
  savePreferencePatch({intro_seen:true});
}
async function maybeShowIntro(){
  var key='kx-intro-v0.10.1';
  var hasLocalIntro=localStorage.getItem(key)==='1';
  try{
    await loadSettingsQuietly(false);
  }catch(e){}
  if(SETTINGS_STATE.intro_seen){
    if(!hasLocalIntro)localStorage.setItem(key,'1');
    return false;
  }
  if(hasLocalIntro){
    SETTINGS_STATE.intro_seen=true;
    savePreferencePatch({intro_seen:true});
    return false;
  }
  if(localStorage.getItem(key)==='1')return false;
  localStorage.setItem(key,'1');
  SETTINGS_STATE.intro_seen=true;
  savePreferencePatch({intro_seen:true});
  showModal(t('introTitle'),introHtml(),'intro-modal',{returnTo:null});
  return true;
}

// 页面可见性变化时刷新数据
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    loadData();
  }
});

init();
