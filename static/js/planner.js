'use strict';
(() => {
  const dataNode = document.getElementById('trip-data');
  if (!dataNode) return;
  let trip = JSON.parse(dataNode.textContent);
  let activeDay = 1, dirty = !trip.id && trip.stops.length > 0, revision = 0, calculation = null, amap = null, mapReady = false, overlays = [];
  let selectedKind = 'scenic', searchVersion = 0, routeVersion = 0;
  const $ = selector => document.querySelector(selector);
  const csrf = $('#csrf-form input').value;
  const kinds = {scenic: '景点', town: '城镇', hotel: '酒店', charging: '充电站', custom: '地点'};
  const kindIcons = {hotel: '⌂', charging: 'ϟ'};
  const element = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const uniqueId = () => (crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-'));
  const timeLabel = minutes => `${Math.floor(minutes / 60)} 小时${minutes % 60 ? ` ${minutes % 60} 分` : ''}`;
  function changed() {
    dirty = true; revision++; routeVersion++; calculation = null;
    $('#save-status').textContent = '有修改尚未保存';
    $('#total-distance').textContent = '—'; $('#total-duration').textContent = '—';
    $('#route-warnings').textContent = '行程已调整，请重新计算路线。';
    $('#charging-suggestions').replaceChildren();
  }
  function readSettings() {
    trip.title = $('#trip-title').value.trim();
    trip.description = $('#trip-description').value.trim();
    trip.start_date = $('#trip-start-date').value || null;
    trip.days = Number($('#trip-days').value);
    trip.travel_mode = $('#trip-mode').value;
    trip.budget = $('#trip-budget').value || '0';
    trip.ev_range_km = Number($('#trip-range').value);
    $('#ev-range-field').hidden = trip.travel_mode !== 'ev';
    $('#editor-heading').textContent = trip.title || '给旅行取个名字';
  }
  function payload() {
    readSettings();
    return Object.fromEntries(['title', 'description', 'start_date', 'days', 'travel_mode', 'budget', 'ev_range_km', 'stops', 'updated_at'].map(key => [key, trip[key]]));
  }
  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeout || 40000);
    try {
      const response = await fetch(url, {...options, signal: controller.signal,
        headers: {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-CSRFToken': csrf, ...options.headers}});
      const json = await response.json().catch(() => ({error: response.status === 403 ? '页面凭证已过期，请先保存本地备份后刷新页面。' : '服务暂时未能响应，请稍后重试。'}));
      if (!response.ok || json.ok === false) {
        if (response.status === 401) throw new Error('登录已过期，请在另一个页面重新登录后，再保存此行程。');
        throw new Error(json.error || '操作没有完成，请稍后再试。');
      }
      return json;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求用时较长，请稍后重试。当前修改仍保留在页面上。');
      if (error instanceof TypeError) throw new Error('网络连接暂时不可用，请检查网络后再试。');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function dayStops(day = activeDay, bridge = true) {
    let stops = trip.stops.filter(stop => stop.day === day);
    const hotels = stops.filter(stop => stop.kind === 'hotel');
    if (hotels.length) stops = [...stops.filter(stop => stop !== hotels.at(-1)), hotels.at(-1)];
    if (bridge) {
      for (let previous = day - 1; previous >= 1; previous--) {
        const previousStops = dayStops(previous, false);
        if (previousStops.length) {
          const last = previousStops.at(-1);
          if (stops.length && (Math.abs(stops[0].lng - last.lng) > .00001 || Math.abs(stops[0].lat - last.lat) > .00001)) {
            stops = [{...last, is_day_start: true, day, stay_minutes: 0}, ...stops];
          }
          break;
        }
      }
    }
    return stops;
  }
  function dayButtons() {
    const nav = $('#day-nav'); nav.replaceChildren();
    for (let day = 1; day <= trip.days; day++) {
      const button = element('button', day === activeDay ? 'active' : '', `第 ${day} 天`);
      button.type = 'button'; button.dataset.day = day;
      button.setAttribute('aria-pressed', day === activeDay ? 'true' : 'false');
      button.addEventListener('click', () => { activeDay = day; render(); });
      nav.append(button);
    }
  }
  function operationButton(symbol, label, action) {
    const button = element('button', 'icon-button', symbol);
    button.type = 'button'; button.title = label; button.setAttribute('aria-label', label);
    button.addEventListener('click', action);
    return button;
  }
  function renderStops() {
    const container = $('#stop-list'); container.replaceChildren();
    const ownStops = trip.stops.filter(stop => stop.day === activeDay);
    const displayed = dayStops();
    if (displayed[0]?.is_day_start) container.append(element('div', 'origin-bridge', `⌂ 从上一天的「${displayed[0].name}」继续出发`));
    if (!ownStops.length) {
      const empty = element('div', 'empty-state');
      empty.append(element('span', 'empty-icon', '↝'), element('h3', '', '把期待的第一站，放进来。'), element('p', '', '添加景点、酒店或充电站，按你的节奏安排这一天。'));
      const add = element('button', 'btn btn-outline btn-small', '＋ 添加第一个地点');
      add.addEventListener('click', openPlaceDialog); empty.append(add); container.append(empty);
    }
    ownStops.forEach((stop, index) => {
      const card = element('article', `stop-card ${stop.kind}`); card.dataset.stopId = stop.id;
      const main = element('div', 'stop-card-main');
      const info = element('div', 'stop-card-info');
      info.append(element('h3', '', stop.name), element('p', 'stop-card-address', stop.address || `${kinds[stop.kind] || '地点'} · 停留 ${stop.stay_minutes} 分钟`));
      main.append(element('span', 'stop-index', kindIcons[stop.kind] || String(index + 1)), info);
      main.append(operationButton('×', `移除${stop.name}`, () => {
        if (!confirm(`从行程中移除「${stop.name}」？`)) return;
        trip.stops = trip.stops.filter(item => item.id !== stop.id); changed(); render();
      }));
      card.append(main);
      const actions = element('div', 'stop-card-actions');
      const navigation = element('a', '', '高德查看 ↗');
      navigation.href = `https://uri.amap.com/marker?position=${stop.lng},${stop.lat}&name=${encodeURIComponent(stop.name)}&coordinate=gaode&callnative=1`;
      navigation.target = '_blank'; navigation.rel = 'noopener noreferrer'; actions.append(navigation);
      const daySelect = element('select'); daySelect.setAttribute('aria-label', `${stop.name}所属天数`);
      for (let day = 1; day <= trip.days; day++) { const option = element('option', '', `第 ${day} 天`); option.value = day; option.selected = stop.day === day; daySelect.append(option); }
      daySelect.addEventListener('change', () => { stop.day = Number(daySelect.value); changed(); render(); });
      actions.append(daySelect);
      const move = direction => {
        const other = ownStops[index + direction]; if (!other) return;
        const first = trip.stops.indexOf(stop), second = trip.stops.indexOf(other);
        [trip.stops[first], trip.stops[second]] = [trip.stops[second], trip.stops[first]];
        changed(); render();
      };
      const up = operationButton('↑', `上移${stop.name}`, () => move(-1)); up.disabled = index === 0;
      const down = operationButton('↓', `下移${stop.name}`, () => move(1)); down.disabled = index === ownStops.length - 1;
      actions.append(up, down); card.append(actions);
      const details = element('details'), summary = element('summary', '', '编辑名称、停留时间和备注'); details.append(summary);
      const nameLabel = element('label', '', '地点名称'), name = element('input'); name.value = stop.name; name.maxLength = 120;
      name.addEventListener('change', () => { if (!name.value.trim()) { name.value = stop.name; return; } stop.name = name.value.trim(); changed(); render(); }); nameLabel.append(name);
      const fieldRow = element('div', 'form-row');
      const typeLabel = element('label', '', '类型'), type = element('select');
      for (const [value, text] of Object.entries(kinds)) { const opt = element('option', '', text); opt.value = value; opt.selected = stop.kind === value; type.append(opt); }
      type.addEventListener('change', () => { stop.kind = type.value; changed(); render(); }); typeLabel.append(type);
      const stayLabel = element('label', '', '停留（分钟）'), stay = element('input'); stay.type = 'number'; stay.min = 0; stay.max = 1440; stay.value = stop.stay_minutes;
      stay.addEventListener('change', () => { if (!stay.checkValidity() || !stay.value) { stay.reportValidity(); stay.value = stop.stay_minutes; return; } stop.stay_minutes = Number(stay.value); changed(); }); stayLabel.append(stay);
      fieldRow.append(typeLabel, stayLabel);
      const noteLabel = element('label', '', '备注'), note = element('textarea'); note.value = stop.note || ''; note.maxLength = 2000; note.rows = 2;
      note.addEventListener('input', () => { stop.note = note.value; changed(); }); noteLabel.append(note);
      details.append(nameLabel, fieldRow, noteLabel); card.append(details); container.append(card);
    });
  }
  const svgElement = (tag, attributes = {}, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  function schematic(stops, currentCalculation) {
    const container = $('#schematic-map'); container.replaceChildren();
    const width = container.clientWidth || 360, height = container.clientHeight || 420;
    const svg = svgElement('svg', {viewBox:`0 0 ${width} ${height}`, role:'img', 'aria-label':`第${activeDay}天行程示意图，连线不代表真实道路`});
    const backdrop = svgElement('g', {transform:`scale(${width/1000} ${height/590})`});
    backdrop.append(svgElement('path',{d:'M0 370Q140 215 267 337T553 281T840 206T1060 324V590H0Z',fill:'#e3e9da'}), svgElement('path',{d:'M0 500Q130 422 325 487T670 402T1000 493V590H0Z',fill:'#dce5d2'}));
    svg.append(backdrop);
    if (!stops.length) {
      svg.append(svgElement('text',{x:width/2,y:height/2-25,'text-anchor':'middle',fill:'#99ac8c','font-size':70},'↝'));
      svg.append(svgElement('text',{x:width/2,y:height/2+22,'text-anchor':'middle',fill:'#697c60','font-size':16},'下一段旅程，还等你落笔。'));
      svg.append(svgElement('text',{x:width/2,y:height/2+52,'text-anchor':'middle',fill:'#87957d','font-size':12},'添加地点，在这里看见你的路线'));
      container.append(svg); return;
    }
    const linePoints = currentCalculation?.polyline?.length ? currentCalculation.polyline : stops.map(s => [s.lng,s.lat]);
    const points = [...linePoints, ...stops.map(s => [s.lng,s.lat])];
    const lngs=points.map(p=>p[0]), lats=points.map(p=>p[1]);
    const centerLat=(Math.min(...lats)+Math.max(...lats))/2;
    const latitudeScale=Math.cos(centerLat*Math.PI/180);
    const xs=lngs.map(x=>x*latitudeScale), minX=Math.min(...xs), maxX=Math.max(...xs), minY=Math.min(...lats), maxY=Math.max(...lats);
    const scale=Math.min((width-130)/Math.max(maxX-minX,.015),(height-135)/Math.max(maxY-minY,.015));
    const project=p=>[width/2+(p[0]*latitudeScale-(minX+maxX)/2)*scale,height/2-((p[1]-(minY+maxY)/2)*scale)];
    const segments=currentCalculation?.legs?.length ? currentCalculation.legs : [{polyline:linePoints,source:'estimate'}];
    for (const segment of segments) {
      const path=segment.polyline.map((p,index)=>`${index?'L':'M'}${project(p).join(' ')}`).join(' ');
      svg.append(svgElement('path',{d:path,fill:'none',stroke:'#ffffffa0','stroke-width':10,'stroke-linejoin':'round'}));
      svg.append(svgElement('path',{d:path,fill:'none',stroke:segment.source==='amap'?'#46785d':'#8c9f78','stroke-width':3.5,'stroke-linecap':'round','stroke-linejoin':'round','stroke-dasharray':segment.source==='amap'?'':'8 9'}));
    }
    stops.forEach((stop,index)=>{
      const [x,y]=project([stop.lng,stop.lat]); const color=stop.kind==='hotel'?'#b99367':stop.kind==='charging'?'#738e49':'#37634f';
      svg.append(svgElement('circle',{cx:x,cy:y,r:14,fill:'#fff',stroke:color,'stroke-width':2}));
      svg.append(svgElement('text',{x,y:y+5,'text-anchor':'middle',fill:color,'font-size':12,'font-family':'sans-serif'},kindIcons[stop.kind]||index+1));
      const truncated=stop.name.length>13?stop.name.slice(0,12)+'…':stop.name;
      const labelY=y+(index%2?32:-25);
      const labelX=Math.max(12+truncated.length*6,Math.min(width-12-truncated.length*6,x));
      const label=svgElement('text',{x:labelX,y:labelY,'text-anchor':'middle',fill:'#34523c','font-size':12,'paint-order':'stroke',stroke:'#edf0e5','stroke-width':6,'stroke-linejoin':'round'},truncated);
      if(stops.length<=15||index%Math.ceil(stops.length/12)===0)svg.append(label);
    });
    container.append(svg);
  }
  function renderMap() {
    const currentCalculation=calculation?.days.find(day=>day.day===activeDay);
    const stops=currentCalculation?.stops || dayStops();
    $('#total-stops').textContent=trip.stops.length;
    $('#map-title').textContent=`第 ${activeDay} 天 · ${trip.stops.filter(s=>s.day===activeDay).length} 个地点`;
    $('#map-source').textContent=calculation?.source==='amap'?'高德道路数据':'路线示意';
    $('#map-note').textContent=currentCalculation?.source==='amap'?'道路来自高德 · 请结合实时导航出发':'路线示意图 · 虚线为直线，不代表实际道路';
    schematic(stops,currentCalculation);
    if (!amap || !mapReady) return;
    try {
      amap.remove(overlays); overlays=[];
      for (const [index, stop] of stops.entries()) {
        const marker=new AMap.Marker({position:[stop.lng,stop.lat],title:stop.name,label:{content:element('span','',`${index+1}. ${stop.name}`).outerHTML,direction:'top'}});
        overlays.push(marker);
      }
      const segments=currentCalculation?.legs?.length?currentCalculation.legs:[{polyline:stops.map(stop=>[stop.lng,stop.lat]),source:'estimate'}];
      for(const segment of segments) if(segment.polyline.length>1) overlays.push(new AMap.Polyline({path:segment.polyline,strokeColor:'#285544',strokeWeight:5,strokeStyle:segment.source==='amap'?'solid':'dashed',showDir:true}));
      if(overlays.length){amap.add(overlays);amap.setFitView(overlays,false,[65,65,65,65],13);}
    } catch { $('#map-note').textContent='地图加载遇到问题，当前保留路线示意图。'; }
  }
  function render() {
    dayButtons(); renderStops(); renderMap();
    $('#day-title').textContent=`第 ${activeDay} 天`;
    const dayCalculation=calculation?.days.find(day=>day.day===activeDay);
    $('#day-meta').textContent=dayCalculation?`${dayCalculation.distance_km} 公里 · 交通 ${timeLabel(dayCalculation.duration_minutes)} · 停留 ${timeLabel(dayCalculation.stay_minutes)}`:'按你的节奏，安排沿途的风景';
  }
  function addStop(place, beforeName=null, day=activeDay) {
    if(trip.stops.length>=150){showToast('每份行程最多添加 150 个地点。',true);return;}
    const stop={id:uniqueId(),name:place.name,lng:Number(place.lng),lat:Number(place.lat),kind:place.kind||selectedKind,day,stay_minutes:place.kind==='charging'?45:place.kind==='hotel'?0:60,note:place.note||'',address:place.address||''};
    const before=beforeName?trip.stops.findIndex(s=>s.day===day&&s.name===beforeName):-1;
    const hotel=trip.stops.findIndex(s=>s.day===day&&s.kind==='hotel');
    const index=before>=0?before:stop.kind!=='hotel'?hotel:-1;
    if(index>=0)trip.stops.splice(index,0,stop);else trip.stops.push(stop);
    activeDay=day;changed();render();showToast(`「${stop.name}」已加入第 ${day} 天`);
  }
  function openPlaceDialog(){
    $('#place-dialog').showModal();
    $('#place-query').focus();
  }
  $('#add-stop').addEventListener('click',openPlaceDialog);
  $('[data-close-dialog]').addEventListener('click',()=>$('#place-dialog').close());
  $('#place-dialog').addEventListener('click',event=>{if(event.target===$('#place-dialog')){const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)event.target.close();}});
  document.querySelectorAll('[data-kind]').forEach(button=>button.addEventListener('click',()=>{
    selectedKind=button.dataset.kind;
    document.querySelectorAll('[data-kind]').forEach(item=>item.classList.toggle('active',item===button));
    $('#place-query').placeholder=selectedKind==='hotel'?'搜索酒店、民宿或城市':selectedKind==='charging'?'搜索充电站或留空查找附近充电站':'输入名称，例如：西湖、观景台';
    searchVersion++;$('#place-results').replaceChildren(element('p','small-muted','输入关键词开始搜索，或在下方手动添加地点。'));
  }));
  $('#manual-place-form').addEventListener('submit',event=>{
    event.preventDefault();const form=event.target;if(!form.reportValidity())return;
    const values=Object.fromEntries(new FormData(form));
    if(!values.name.trim()){showToast('请填写地点名称。',true);return;}
    addStop({...values,name:values.name.trim(),kind:selectedKind});form.reset();$('#place-dialog').close();
  });
  $('#place-search-form').addEventListener('submit',async event=>{
    event.preventDefault();const results=$('#place-results'),button=event.target.querySelector('button');
    const current=++searchVersion;const searchedKind=selectedKind;
    const params=new URLSearchParams({q:$('#place-query').value.trim(),kind:searchedKind});
    const last=dayStops().filter(s=>s.kind!=='hotel').at(-1)||dayStops().at(-1);
    if($('#search-nearby').checked&&last){params.set('lng',last.lng);params.set('lat',last.lat);}
    results.replaceChildren(element('p','small-muted','正在寻找沿途的好去处…'));button.disabled=true;
    try{
      const response=await request(`/api/places/?${params}`,{timeout:15000});if(current!==searchVersion)return;
      results.replaceChildren();
      if(!response.places.length)results.append(element('p','small-muted','没有找到匹配地点。试试完整名称、附近城市，或手动添加。'));
      response.places.forEach(place=>{
        const row=element('div','place-result'),info=element('div');info.append(element('strong','',place.name),element('p','',place.address||'地址信息待确认'));
        const add=element('button','btn btn-outline btn-small','＋ 添加');add.type='button';
        add.addEventListener('click',()=>{addStop({...place,kind:searchedKind});$('#place-dialog').close();});row.append(info,add);results.append(row);
      });
    }catch(error){if(current===searchVersion)results.replaceChildren(element('div','notice',error.message));}
    finally{button.disabled=false;}
  });
  $('#trip-mode').value=trip.travel_mode||'driving';$('#ev-range-field').hidden=trip.travel_mode!=='ev';
  $('#trip-days').value=trip.days||3;
  document.querySelectorAll('.settings-fields input,.settings-fields select,.settings-fields textarea').forEach(input=>{
    if(input.id==='trip-days')return;
    input.addEventListener('input',()=>{readSettings();changed();renderMap();});
  });
  $('#trip-days').addEventListener('change',event=>{
    const field=event.target;const days=Number(field.value);
    if(!Number.isInteger(days)||days<1||days>30){showToast('旅行天数需要在 1 至 30 天之间。',true);field.value=trip.days;return;}
    if(trip.stops.some(stop=>stop.day>days)){
      if(!confirm(`缩短为 ${days} 天后，后续地点会移动到第 ${days} 天。是否继续？`)){field.value=trip.days;return;}
      trip.stops.forEach(stop=>{if(stop.day>days)stop.day=days;});
    }
    trip.days=days;activeDay=Math.min(activeDay,days);changed();render();
  });
  function validateFields(){
    const fields=[...document.querySelectorAll('.settings-fields input')];
    for(const input of fields)if(!input.checkValidity()){input.reportValidity();return false;}
    if(!$('#trip-title').value.trim()){showToast('先给旅行取个名字吧。',true);$('#trip-title').focus();return false;}
    return true;
  }
  function detailLink(){
    if(!trip.id)return;
    let link=$('#view-trip');if(!link){link=element('a','text-link','查看行程 ↗');link.id='view-trip';$('.planner-top-actions').prepend(link);}
    link.href=`/trips/${trip.id}/`;
  }
  $('#save-trip').addEventListener('click',async()=>{
    if(!validateFields())return;
    const button=$('#save-trip'),snapshot=JSON.stringify(payload()),version=revision;
    button.disabled=true;button.textContent='保存中…';
    try{
      const response=await request(trip.id?`/api/trips/${trip.id}/save/`:'/api/trips/',{method:'POST',body:snapshot});
      if(revision===version){trip=response.trip;dirty=false;$('#save-status').textContent='已保存到账号';}
      else{trip.id=response.trip.id;trip.updated_at=response.trip.updated_at;$('#save-status').textContent='保存期间有新修改，请再次保存';}
      history.replaceState(null,'',`/trips/${trip.id}/edit/`);if(trip.stops.length&&!dirty)$('.trip-settings').open=false;detailLink();showToast(dirty?'已有内容已保存，请继续保存最新修改。':'行程已安全保存，可以在其他设备继续查看。');
    }catch(error){showToast(error.message,true);$('#save-status').textContent='尚未保存，请重试';}
    finally{button.disabled=false;button.textContent='保存行程';}
  });
  $('#calculate-trip').addEventListener('click',async()=>{
    if(!validateFields())return;
    const button=$('#calculate-trip');const snapshot=payload();const version=++routeVersion;
    button.disabled=true;button.textContent='计算中…';
    try{
      const response=await request('/api/directions/',{method:'POST',body:JSON.stringify({stops:snapshot.stops,days:snapshot.days,travel_mode:snapshot.travel_mode,ev_range_km:snapshot.ev_range_km})});
      if(version!==routeVersion){showToast('计算期间行程发生变化，请重新计算。');return;}
      calculation=response.itinerary;
      $('#total-distance').textContent=calculation.distance_km;
      $('#total-duration').textContent=`${(calculation.duration_minutes/60).toFixed(1)} 小时`;
      const warnings=$('#route-warnings');warnings.replaceChildren();
      if(calculation.warnings.length){const list=element('ul');calculation.warnings.forEach(message=>list.append(element('li','',message)));warnings.append(list);}
      else warnings.textContent='路线已按高德道路数据计算。驾驶时间不包含游玩、休息和实时拥堵变化。';
      const suggestions=$('#charging-suggestions');suggestions.replaceChildren();
      for(const day of calculation.days){
        if(!day.charging_suggestions.length)continue;
        suggestions.append(element('h3','',`第 ${day.day} 天 · 可考虑的充电停靠`));
        for(const place of day.charging_suggestions){
          const row=element('div','place-result'),info=element('div');info.append(element('strong','',place.name),element('p','',`在「${place.before_stop}」之前补能 · 需确认可用性和绕行距离`));
          const add=element('button','btn btn-outline btn-small','加入路线');add.addEventListener('click',()=>addStop(place,place.before_stop,day.day));row.append(info,add);suggestions.append(row);
        }
      }
      render();showToast(calculation.source==='amap'?'路线计算完成。':'当前结果为直线估算，详细说明见地图下方。');
      if(innerWidth<=680)switchView('map');
    }catch(error){showToast(error.message,true);}
    finally{button.disabled=false;button.textContent='↝ 计算路线';}
  });
  function switchView(view){
    $('.planner-shell').classList.toggle('show-map',view==='map');
    document.querySelectorAll('[data-view]').forEach(button=>button.classList.toggle('active',button.dataset.view===view));
    if(view==='map'){renderMap();if(amap&&mapReady)requestAnimationFrame(()=>amap.resize?.());}
  }
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>switchView(button.dataset.view)));
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  async function initializeMap(){
    try{
      const config=await request('/api/config/',{timeout:8000});if(!config.amap_js_key)return;
      window._AMapSecurityConfig={serviceHost:location.origin+config.security_proxy};
      const script=document.createElement('script');script.src=`https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(config.amap_js_key)}`;
      script.referrerPolicy='strict-origin-when-cross-origin';
      await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('地图载入超时')),15000);script.onload=()=>{clearTimeout(timeout);resolve();};script.onerror=()=>{clearTimeout(timeout);reject(new Error('地图暂不可用'));};document.head.append(script);});
      amap=new AMap.Map('map-canvas',{zoom:5,center:[108.9,34.2],viewMode:'2D',resizeEnable:true});
      amap.on('complete',()=>{mapReady=true;$('#schematic-map').hidden=true;renderMap();});
    }catch{ $('#map-note').textContent='地图暂不可用 · 当前显示路线示意，连线不代表实际道路'; }
  }
  // A local download keeps unsaved edits recoverable without placing private journeys in browser storage.
  const backup=element('button','text-button','↓ 导出当前草稿');backup.type='button';backup.title='即使断网也能备份尚未保存的修改';
  backup.addEventListener('click',()=>{
    const data={...payload(),schema_version:1,coordinate_system:'GCJ-02'};delete data.updated_at;
    const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=element('a');link.href=url;link.download='我的旅行草稿.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);showToast('草稿已下载，可在“导入路线”中恢复。');
  });$('.editor-help').after(backup);
  let resizeTimer;
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(renderMap,150);});
  if(trip.id&&trip.stops.length)$('.trip-settings').open=false;
  render();detailLink();initializeMap();
})();
