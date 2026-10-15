
  const $ = (id) => document.getElementById(id);
  const app = {health: null, runs: [], status: null, inferenceJob: null, inferenceResult: null, inferenceSelectionId: 0, videoSelectionId: 0, selectedVideo: null, mediaJob: null, pollTimer: null, uploadUrl: null, lastLog: "", lastChartKey: "", selectedRunsRoot: ""};
  const reverseApp = {summary: null, runs: [], status: null, lastLog: "", lastChartKey: "", selectedRunsRoot: "", polling: false, configCheckpoint: ""};
  const fourApp = {summary: null, runs: [], status: null, lastLog: "", lastChartKey: "", selectedRunsRoot: "", polling: false, configCheckpoint: ""};
  const modeDescriptions = {
    sensitive: "Sensitive：从验证集选择 F2 最高的阈值，优先提高 Recall、减少漏报，通常会增加误报。",
    moderate: "Moderate：从验证集选择 F1 最高的阈值，平衡 Precision 与 Recall。",
    certain: "Certain：从验证集选择 F0.5 最高的阈值，更重视 Precision、减少误报，通常会增加漏报。"
  };
  const iconCheck = (ok) => ok ? "good" : "bad";
  function showBanner(id, message, type="info") { const el=$(id); el.textContent=message; el.className=`banner show ${type}`; }
  function hideBanner(id) { $(id).className="banner"; $(id).textContent=""; }
  function fmt(value, digits=3) { if(value===null||value===undefined||value==="")return "—";const n=Number(value); return Number.isFinite(n) ? n.toFixed(digits) : "—"; }
  function pct(value, digits=1) { if(value===null||value===undefined||value==="")return "—";const n=Number(value); return Number.isFinite(n) ? `${(n*100).toFixed(digits)}%` : "—"; }
  function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
  async function api(url, options={}) {
    const response=await fetch(url, options); let payload={};
    try { payload=await response.json(); } catch (_) { payload={error:`服务器返回 HTTP ${response.status}`}; }
    if(!response.ok) throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
    return payload;
  }
  function switchPage(name) {
    document.querySelectorAll(".page").forEach(el=>el.classList.toggle("active",el.id===`page-${name}`));
    document.querySelectorAll(".nav-button").forEach(el=>{ const active=el.dataset.page===name; el.classList.toggle("active",active); if(active) el.setAttribute("aria-current","page"); else el.removeAttribute("aria-current"); });
    if(name==="inference") loadRuns(true);
    if(name==="reverse") { loadReverseDatasetSummary(); pollReverseStatus(); }
    if(name==="four") { loadFourDatasetSummary(); pollFourStatus(); }
  }
  document.querySelectorAll(".nav-button").forEach(button=>button.addEventListener("click",()=>switchPage(button.dataset.page)));

  function reverseRoots() {
    return {
      legacy_dataset_root:$("reverseLegacyRoot").value.trim(),
      ucf_root:$("reverseUcfRoot").value.trim(),
      airtlab_root:$("reverseAirRoot").value.trim(),
      guardaid_root:$("reverseGuardRoot").value.trim(),
      output_root:$("reverseOutputRoot").value.trim()
    };
  }
  async function loadReverseDatasetSummary() {
    try {
      const query=new URLSearchParams(reverseRoots());
      const summary=await api(`/api/reverse/summary?${query.toString()}`);
      reverseApp.summary=summary;
      if(!$("reverseLegacyRoot").value) $("reverseLegacyRoot").value=summary.roots.legacy;
      if(!$("reverseUcfRoot").value) $("reverseUcfRoot").value=summary.roots.ucf;
      if(!$("reverseAirRoot").value) $("reverseAirRoot").value=summary.roots.airtlab;
      if(!$("reverseGuardRoot").value) $("reverseGuardRoot").value=summary.roots["guardaid"];
      if(!$("reverseOutputRoot").value) $("reverseOutputRoot").value=summary.roots.output;
      renderReverseDatasetSummary(summary);
      if(!summary.ready) showBanner("reverseBanner","数据路径尚未齐全。请确认三个所选数据集目录、UCF 两类目录、外部测试路径，以及既有两秒帧缓存。","warn");
      else if($("reverseBanner").classList.contains("warn") && !reverseApp.status?.process_active) hideBanner("reverseBanner");
      await loadReverseRuns(true);
    } catch(err) {
      showBanner("reverseBanner",`无法读取数据路径：${err.message}`,"error");
      $("reverseSourceSummary").innerHTML='<div class="source-summary"><strong>数据路径检查失败</strong><span>请确认路径存在且本地服务仍在运行。</span></div>';
      reverseApp.summary=null;
      updateReverseControls();
    }
  }
  function renderReverseDatasetSummary(data) {
    const u=data.ucf||{}, a=data.airtlab||{}, g=data["guardaid"]||{}, splits=u.split_preview||{};
    const legacy=data.legacy||{}, legacyDatasets=legacy.datasets||[];
    const fightSplits=["train","val","test"].map(key=>`${({train:"Train",val:"Val",test:"Test"})[key]} ${splits[key]?.Fight??"—"}`).join(" · ");
    const normalSplits=["train","val","test"].map(key=>`${({train:"Train",val:"Val",test:"Test"})[key]} ${splits[key]?.NonFight??"—"}`).join(" · ");
    const archives=(u.normal_archives||[]).reduce((total,row)=>total+Number(row.videos||0),0);
    const legacyCards=legacyDatasets.map(item=>{
      const splitLine=(split,label)=>`${({train:"Train",val:"Val",test:"Test"})[split]} ${label} ${Number(item.splits?.[split]?.[label]||0).toLocaleString()}`;
      return `<div class="source-summary"><strong>${escapeHtml(item.name)} · 保留原 split</strong><span class="source-count">${Number(item.total||0).toLocaleString()} 段</span><span>${splitLine("train","Fight")} / ${splitLine("train","NonFight")}<br>${splitLine("val","Fight")} / ${splitLine("val","NonFight")}<br>${splitLine("test","Fight")} / ${splitLine("test","NonFight")}${item.missing?.length?`<br><span class="text-danger">缺少 ${item.missing.length} 个类别目录</span>`:""}</span></div>`;
    }).join("");
    $("reverseSourceSummary").innerHTML=`${legacyCards}
      <div class="source-summary"><strong>UCF · 80/10/10</strong><span class="source-count">${Number(u.raw_fight||0).toLocaleString()} Fight · ${Number(u.raw_nonfight_including_archive||0).toLocaleString()} NonFight</span><span>暴力子类：Assault ${Number(u.assault_videos||0).toLocaleString()} · Fighting ${Number(u.fighting_videos||0).toLocaleString()}。正常视频文件夹 ${Number(u.nonfight_folder_videos||0).toLocaleString()}${archives?` · 压缩包 ${archives.toLocaleString()}`:""}<br>${fightSplits}<br>${normalSplits}</span></div>
      <div class="source-summary"><strong>AIRTLab · 有标签外部测试</strong><span class="source-count">${Number(a.total||0).toLocaleString()} 段</span><span>Fight ${Number(a.fight||0).toLocaleString()} · NonFight ${Number(a.nonfight||0).toLocaleString()}<br>仅在训练后评估，不参与训练、验证或阈值选择。</span></div>
      <div class="source-summary"><strong>GuardAId · 无标签外部测试</strong><span class="source-count">${Number(g.videos||0).toLocaleString()} 段</span><span>没有 ground truth 标签，只保存预测；可另列“假设全为 NonFight”的条件性误报率。</span></div>`;
    const free=data.storage?.free_gib, uncompressed=data.storage?.normal_archive_uncompressed_gib;
    const normalFolder=Number(u.nonfight_folder_videos||0);
    let storageText=`可用空间 ${free===null||free===undefined?"—":`${fmt(free,1)} GiB`}`;
    if(normalFolder) storageText+=` · 已识别 Non-Violence 文件夹中的 ${normalFolder.toLocaleString()} 段视频`;
    else if(Number(u.normal_archive_videos||0)) storageText+=` · 程序逐条临时读取压缩包，无需完整解压${uncompressed?`（整包解压约 ${fmt(uncompressed,1)} GiB）`:""}`;
    $("reverseStorageNote").textContent=storageText;
    updateReverseControls();
  }
  function updateReverseControls() {
    const status=reverseApp.status?.status||"idle";
    const active=Boolean(reverseApp.status?.process_active)||["starting","running","pause_requested","stopping"].includes(status);
    const resumeSelected=Boolean($("reverseResumeRun")?.value);
    $("reverseStartButton").disabled=active||!reverseApp.summary?.ready||!app.health?.model_ready;
    $("reversePauseButton").disabled=!active||status==="pause_requested"||status==="stopping";
    $("reverseCancelPauseButton").hidden=status!=="pause_requested";
    $("reverseStopButton").disabled=!active;
    $("reverseResumeRun").disabled=active;
    $("reverseHistoryRun").disabled=active;
    $("reverseStartButton").textContent=resumeSelected?"从 checkpoint 恢复训练":"开始 2 秒方案训练";
    const locked=resumeSelected;
    ["reverseLegacyRoot","reverseUcfRoot","reverseAirRoot","reverseGuardRoot","reverseOutputRoot","reverseEpochs","reversePatience","reverseMinDelta","reverseBackboneLr","reverseHeadLr","reverseWeightDecay","reverseGradAccum","reverseWindowBatchSize","reverseSeed","reverseDevice","reverseCacheDir","reverseAmp","reverseAllowCpu"].forEach(id=>$(id).disabled=active||locked);
  }

  async function loadHealth() {
    try {
      const h=await api("/api/health"); app.health=h;
      $("pythonVersion").textContent=`Python ${h.python_version} · ${h.python}`;
      $("datasetRoot").value ||= h.dataset_root;
      $("outputRoot").value ||= h.output_root;
      $("inferOutputRoot").value ||= h.output_root;
      const missing=Object.entries(h.dependencies).filter(([,ok])=>!ok).map(([name])=>name);
      $("healthDot").className=`dot ${missing.length?"warn":"good"}`;
      $("healthText").textContent=missing.length?`缺少依赖：${missing.join(", ")}`:"本机服务已连接";
      if(missing.length) showBanner("trainBanner",`当前 Python 环境缺少：${missing.join(", ")}。页面可以打开，但训练或视频推理无法开始。请在此 Python 环境安装 model_code/requirements-mc3.txt，并安装匹配版本的 PyTorch 与 TorchVision。`,"warn");
      else hideBanner("trainBanner");
      updateReverseControls();
    } catch(err) {
      $("healthDot").className="dot bad"; $("healthText").textContent="本机服务未连接"; $("pythonVersion").textContent="请确认 dashboard 终端仍在运行";
    }
  }
  function activeRunValue(select) { return select.value; }
  function updateInferenceProtocol(run) {
    const tag=$("inferProtocolTag"), note=$("inferProtocolNote");
    if(!run) { tag.textContent="等待选择模型"; note.textContent="选择模型后会显示对应的窗口、步长、帧数和短视频处理规则。"; return; }
    const seconds=Number(run.window_seconds), stride=Number(run.stride_seconds), frames=Number(run.frames_per_window);
    if(seconds===4 && stride===2 && frames===16) {
      tag.textContent="4 秒窗口 · 2 秒步长 · 每窗 16 帧";
      note.textContent="长视频按 2 秒步长滑动，必要时追加末尾对齐窗口；短片在真实时长内均匀采样 16 帧，不补黑帧。";
    } else if(seconds===2 && frames===8) {
      tag.textContent="2 秒窗口 · 1 秒步长 · 每窗 8 帧";
      note.textContent="按完整的 2 秒自然视频窗口、1 秒步长、每窗 8 帧进行推理；不足 2 秒的视频不会补帧，尾部不完整窗口会舍弃。";
    } else if(seconds===1 && frames===4) {
      tag.textContent="1 秒窗口 · 0.5 秒步长 · 每窗 4 帧";
      note.textContent="按完整的 1 秒自然视频窗口、0.5 秒步长、每窗 4 帧进行推理；不足 1 秒的视频不会补帧。";
    } else {
      tag.textContent="模型协议已读取";
      note.textContent=`模型窗口 ${Number.isFinite(seconds)?seconds:"—"} 秒 · 步长 ${Number.isFinite(stride)?stride:"—"} 秒 · 每窗 ${Number.isFinite(frames)?frames:"—"} 帧。`;
    }
  }
  async function loadRuns(force=false) {
    const inferencePageActive=$("page-inference").classList.contains("active");
    const outputRoot=inferencePageActive
      ? ($("inferOutputRoot").value.trim() || $("outputRoot").value.trim() || app.health?.output_root || "")
      : ($("outputRoot").value.trim() || app.health?.output_root || "");
    const rootChanged=app.selectedRunsRoot!==outputRoot;
    if(!force && !rootChanged && app.lastRunsLoad && Date.now()-app.lastRunsLoad<12000) return;
    app.selectedRunsRoot=outputRoot; app.lastRunsLoad=Date.now();
    try {
      const encoded=encodeURIComponent(outputRoot);
      const {runs}=await api(`/api/runs?output_root=${encoded}`); app.runs=runs;
      const resumeSelect=$("resumeRun"), oldResume=resumeSelect.value;
      resumeSelect.innerHTML='<option value="">新建训练</option>';
      runs.filter(run=>run.last_checkpoint && run.status!=="completed").forEach(run=>{
        const option=document.createElement("option"); option.value=run.last_checkpoint; option.textContent=`${run.name} · ${run.status==="resumable"?"可恢复":"进行中/未完成"}`; resumeSelect.appendChild(option);
      });
      if([...resumeSelect.options].some(option=>option.value===oldResume)) resumeSelect.value=oldResume;
      const inferSelect=$("inferRun"), oldInfer=inferSelect.value;
      const inferRuns=runs.filter(run=>run.can_infer);
      inferSelect.innerHTML=inferRuns.length?"":'<option value="">没有已完成的可推理模型</option>';
      inferRuns.forEach(run=>{const option=document.createElement("option");option.value=run.best_model;const protocol=run.window_seconds===4?" · 4秒/16帧":run.window_seconds===2?" · 2秒/8帧":run.window_seconds===1?" · 1秒/4帧":"";option.textContent=`${run.name}${run.best_epoch?` · 最佳 Epoch ${run.best_epoch}`:""}${protocol}`;inferSelect.appendChild(option);});
      if([...inferSelect.options].some(option=>option.value===oldInfer)) inferSelect.value=oldInfer;
      updateInferenceProtocol(inferRuns.find(run=>run.best_model===inferSelect.value));
      if(!inferRuns.length && runs.length) $("resultModelTag").textContent="需要完成验证与测试";
      refreshResumeHint();
    } catch(err) { /* keep the current choices; the folder may not exist yet */ }
  }
  function refreshResumeHint() {
    const checkpoint=$("resumeRun").value;
    const selected=app.runs.find(run=>run.last_checkpoint===checkpoint);
    $("startButton").querySelector("span").textContent=checkpoint?"继续训练":"开始训练";
    $("resumeBox").classList.toggle("show",Boolean(checkpoint || app.status?.last_checkpoint));
    if(checkpoint) $("checkpointPath").textContent=checkpoint;
    else if(app.status?.last_checkpoint) $("checkpointPath").textContent=app.status.last_checkpoint;
    if(selected) loadResumeConfig(selected).catch(err=>showBanner("trainBanner",err.message,"error"));
    setResumeFields(Boolean(checkpoint));
  }
  let resumeFetchPath="";
  async function loadResumeConfig(run) {
    if(resumeFetchPath===run.last_checkpoint) return;
    resumeFetchPath=run.last_checkpoint;
    const root=encodeURIComponent($("outputRoot").value.trim() || app.health?.output_root || "");
    const url=`/api/run-config?run_dir=${encodeURIComponent(run.run_dir)}&output_root=${root}`;
    const {config}=await api(url);
    $("datasetRoot").value=config.dataset_root || $("datasetRoot").value;
    $("cacheDir").value=config.cache_dir || "";
    const device=String(config.device||"auto"); $("device").value=device.startsWith("cpu")?"cpu":device.startsWith("cuda")?"cuda":"auto";
    $("epochs").value=config.epochs??30; $("patience").value=config.patience??6; $("minDelta").value=config.min_delta??0.001;
    $("backboneLr").value=config.backbone_lr??0.00002; $("headLr").value=config.head_lr??0.0001; $("weightDecay").value=config.weight_decay??0.0001;
    $("gradAccum").value=config.grad_accum??8; $("windowBatchSize").value=config.window_batch_size??8; $("seed").value=config.seed??42;
    $("amp").checked=Boolean(config.amp); $("allowCpu").checked=$("device").value==="cpu";
    showBanner("trainBanner","已加载 checkpoint 的数据路径与训练参数。恢复时会继续使用保存状态。","info");
  }
  function setResumeFields(resuming) {
    ["datasetRoot","outputRoot","cacheDir","epochs","patience","minDelta","backboneLr","headLr","weightDecay","gradAccum","windowBatchSize","seed","device","amp","allowCpu"].forEach(id=>$(id).disabled=resuming);
    if(!resuming && !$("trainBanner").classList.contains("error")) hideBanner("trainBanner");
  }
  $("resumeRun").addEventListener("change",()=>{resumeFetchPath="";refreshResumeHint();});
  $("outputRoot").addEventListener("change",()=>{app.lastRunsLoad=0;loadRuns(true);});
  $("inferOutputRoot").addEventListener("change",()=>{app.selectedRunsRoot="";loadRuns(true);});
  $("refreshRunsButton").addEventListener("click",()=>{app.lastRunsLoad=0;loadRuns(true);});

  async function loadReverseRuns(force=false) {
    const outputRoot=$("reverseOutputRoot").value.trim()||reverseApp.summary?.roots?.output||"";
    if(!outputRoot)return;
    if(!force&&reverseApp.selectedRunsRoot===outputRoot&&reverseApp.lastRunsLoad&&Date.now()-reverseApp.lastRunsLoad<10000)return;
    reverseApp.selectedRunsRoot=outputRoot;reverseApp.lastRunsLoad=Date.now();
    try {
      const {runs}=await api(`/api/reverse/runs?output_root=${encodeURIComponent(outputRoot)}`);
      reverseApp.runs=runs||[];
      const historySelect=$("reverseHistoryRun"),oldHistory=historySelect.value;
      historySelect.innerHTML=reverseApp.runs.length?"":'<option value="">尚无倒放拼接运行记录</option>';
      reverseApp.runs.forEach(run=>{
        const option=document.createElement("option");option.value=run.run_dir;
        const result=run.best_validation_roc_auc===null||run.best_validation_roc_auc===undefined?"":` · 最佳 AUC ${fmt(run.best_validation_roc_auc,3)}`;
        option.textContent=`${run.name} · ${run.status}${result}`;historySelect.appendChild(option);
      });
      if([...historySelect.options].some(option=>option.value===oldHistory))historySelect.value=oldHistory;
      else if(reverseApp.runs.length)historySelect.value=reverseApp.runs[0].run_dir;
      const select=$("reverseResumeRun"),old=select.value;
      select.innerHTML='<option value="">新建运行</option>';
      reverseApp.runs.filter(run=>run.last_checkpoint&&run.status!=="completed").forEach(run=>{
        const option=document.createElement("option");option.value=run.last_checkpoint;
        option.textContent=`${run.name} · ${run.status==="resumable"?"可恢复":"未完成"}`;select.appendChild(option);
      });
      if([...select.options].some(option=>option.value===old))select.value=old;
      updateReverseControls();
      if(historySelect.value&&historySelect.value!==oldHistory)pollReverseStatus();
      if(select.value)await loadReverseResumeConfig();
    } catch(err) { /* keep the last visible history if the output folder is temporarily unavailable */ }
  }
  async function loadReverseResumeConfig() {
    const checkpoint=$("reverseResumeRun").value;
    if(!checkpoint){reverseApp.configCheckpoint="";updateReverseControls();return;}
    if(reverseApp.configCheckpoint===checkpoint){updateReverseControls();return;}
    const root=$("reverseOutputRoot").value.trim()||reverseApp.summary?.roots?.output||"";
    const runDir=checkpoint.replace(/[\\/][^\\/]+$/,"" );
    const {config}=await api(`/api/reverse/run-config?run_dir=${encodeURIComponent(runDir)}&output_root=${encodeURIComponent(root)}`);
    const roots=config.preprocessing?.source_roots||{};
    $("reverseLegacyRoot").value=roots.legacy||reverseApp.summary?.roots?.legacy||$("reverseLegacyRoot").value;
    $("reverseUcfRoot").value=roots.ucf||$("reverseUcfRoot").value;
    $("reverseAirRoot").value=roots.airtlab||$("reverseAirRoot").value;
    $("reverseGuardRoot").value=roots["guardaid"]||$("reverseGuardRoot").value;
    $("reverseOutputRoot").value=root;
    $("reverseEpochs").value=config.epochs??30;$("reversePatience").value=config.patience??6;$("reverseMinDelta").value=config.min_delta??0.001;
    $("reverseBackboneLr").value=config.backbone_lr??0.00002;$("reverseHeadLr").value=config.head_lr??0.0001;$("reverseWeightDecay").value=config.weight_decay??0.0001;
    $("reverseGradAccum").value=config.grad_accum??8;$("reverseWindowBatchSize").value=config.window_batch_size??4;$("reverseSeed").value=config.seed??2026;
    const device=String(config.device||"auto");$("reverseDevice").value=device.startsWith("cpu")?"cpu":device.startsWith("cuda")?"cuda":"auto";
    $("reverseAmp").checked=Boolean(config.amp);$("reverseAllowCpu").checked=$("reverseDevice").value==="cpu";
    $("reverseCacheDir").value=config.cache_dir||"";
    reverseApp.configCheckpoint=checkpoint;updateReverseControls();
    showBanner("reverseBanner","已载入所选运行的训练配置和数据来源；恢复时沿用原 checkpoint 设置。","info");
  }
  function collectReverseConfig() {
    return {
      ...reverseRoots(),resume_checkpoint:$("reverseResumeRun").value,
      epochs:$("reverseEpochs").value,patience:$("reversePatience").value,min_delta:$("reverseMinDelta").value,
      backbone_lr:$("reverseBackboneLr").value,head_lr:$("reverseHeadLr").value,weight_decay:$("reverseWeightDecay").value,
      grad_accum:$("reverseGradAccum").value,window_batch_size:$("reverseWindowBatchSize").value,seed:$("reverseSeed").value,
      device:$("reverseDevice").value,cache_dir:$("reverseCacheDir").value.trim(),amp:$("reverseAmp").checked,allow_cpu:$("reverseAllowCpu").checked
    };
  }
  $("reverseResumeRun").addEventListener("change",()=>{reverseApp.configCheckpoint="";loadReverseResumeConfig().catch(err=>showBanner("reverseBanner",err.message,"error"));updateReverseControls();});
  $("reverseHistoryRun").addEventListener("change",()=>pollReverseStatus());
  ["reverseLegacyRoot","reverseUcfRoot","reverseAirRoot","reverseGuardRoot"].forEach(id=>$(id).addEventListener("change",()=>loadReverseDatasetSummary()));
  $("reverseOutputRoot").addEventListener("change",()=>{reverseApp.selectedRunsRoot="";reverseApp.configCheckpoint="";loadReverseDatasetSummary();loadReverseRuns(true);pollReverseStatus();});
  $("reverseRefreshDataButton").addEventListener("click",()=>loadReverseDatasetSummary());
  $("reverseRefreshRunsButton").addEventListener("click",()=>loadReverseRuns(true));
  $("reverseStartButton").addEventListener("click",async()=>{
    const button=$("reverseStartButton");button.disabled=true;hideBanner("reverseBanner");
    try {
      const result=await postJson("/api/reverse/training/start",collectReverseConfig());
      showBanner("reverseBanner",result.resuming?"倒放拼接训练已恢复，将从 checkpoint 的下一个完整 epoch 继续。":"倒放拼接训练已启动，正在建立视频清单并准备帧缓存。","info");
      $("reverseRunPathLabel").textContent=`运行目录：${result.run_dir}`;
      if(![...$("reverseHistoryRun").options].some(option=>option.value===result.run_dir)){const option=document.createElement("option");option.value=result.run_dir;option.textContent=result.run_dir.split(/[\\/]/).pop()+" · running";$("reverseHistoryRun").appendChild(option);}
      $("reverseHistoryRun").value=result.run_dir;
      reverseApp.selectedRunsRoot="";await pollReverseStatus();
    } catch(err) { showBanner("reverseBanner",err.message,"error"); }
    finally { updateReverseControls(); }
  });
  $("reversePauseButton").addEventListener("click",async()=>{try{const result=await postJson("/api/reverse/training/pause");showBanner("reverseBanner",result.message,"info");await pollReverseStatus();}catch(err){showBanner("reverseBanner",err.message,"error");}});
  $("reverseCancelPauseButton").addEventListener("click",async()=>{try{const result=await postJson("/api/reverse/training/cancel-pause");showBanner("reverseBanner",result.message,"info");await pollReverseStatus();}catch(err){showBanner("reverseBanner",err.message,"error");}});
  $("reverseStopButton").addEventListener("click",async()=>{
    if(!window.confirm("立即停止会中断当前 epoch；最近一次完整 checkpoint 可供恢复。确定停止吗？"))return;
    try{const result=await postJson("/api/reverse/training/stop");showBanner("reverseBanner",result.message,"warn");await pollReverseStatus();}catch(err){showBanner("reverseBanner",err.message,"error");}
  });
  $("reverseCopyRunButton").addEventListener("click",async()=>{
    const path=reverseApp.status?.run_dir;if(!path)return;
    try{await navigator.clipboard.writeText(path);$("reverseCopyRunButton").textContent="已复制";setTimeout(()=>$("reverseCopyRunButton").textContent="复制运行路径",1400);}
    catch(_){showBanner("reverseBanner",`运行目录：${path}`,"info");}
  });
  function renderReverseEpochTable(history,bestEpoch) {
    const body=$("reverseEpochTable");
    if(!history.length){body.innerHTML='<tr><td colspan="7" class="empty-state">完成第一个 epoch 后显示记录。</td></tr>';return;}
    body.innerHTML=[...history].reverse().map(row=>`<tr><td>${row.epoch??"—"}</td><td>${fmt(row.train_loss,4)}</td><td>${fmt(row.train_roc_auc,3)}</td><td>${fmt(row.val_roc_auc,4)}</td><td>${fmt(row.val_f1_at_0_5,3)}</td><td>${fmt(row.seconds,1)} s</td><td>${Number(row.epoch)===Number(bestEpoch)?'<span class="tag protocol">最佳</span>':Number(row.improved)===1?"提升":"—"}</td></tr>`).join("");
  }
  function renderReverseHistory(history,bestEpoch) {
    $("reverseHistoryCount").textContent=`${history.length} 个 epoch${bestEpoch?` · 最佳 ${bestEpoch}`:""}`;
    const key=history.map(row=>`${row.epoch}:${row.train_loss}:${row.val_roc_auc}`).join("|");
    if(key===reverseApp.lastChartKey)return;reverseApp.lastChartKey=key;
    drawLineChart($("reverseLossChart"),history.map(row=>row.train_loss===null?NaN:Number(row.train_loss)),"#087f78",false);
    drawLineChart($("reverseAucChart"),history.map(row=>row.val_roc_auc===null?NaN:Number(row.val_roc_auc)),"#328e67",true);
  }
  function renderReverseFinalReport(report) {
    const thresholds=report?.thresholds||{},validation=report?.validation_metrics||{},testing=report?.test_metrics||{};
    const modes=["sensitive","moderate","certain"],names={sensitive:"Sensitive",moderate:"Moderate",certain:"Certain"};
    if(!Object.keys(thresholds).length){$("reverseThresholdTable").innerHTML='<tr><td colspan="13" class="empty-state">完成训练及最终评估后显示。</td></tr>';}
    else $("reverseThresholdTable").innerHTML=modes.map(mode=>{
      const t=thresholds[mode]||{},v=validation[mode]||{},m=testing[mode]||{};
      return `<tr><td>${names[mode]}</td><td>${escapeHtml(t.objective||"—")}</td><td>${fmt(t.threshold,4)}</td><td>${fmt(t.validation_score,3)}</td><td>${fmt(v.precision,3)}</td><td>${fmt(v.recall,3)}</td><td>${fmt(v.f1,3)}</td><td>${fmt(v.roc_auc,3)}</td><td>${m.n??"—"}</td><td>${fmt(m.precision,3)}</td><td>${fmt(m.recall,3)}</td><td>${fmt(m.f1,3)}</td><td>${fmt(m.roc_auc,3)}</td></tr>`;
    }).join("");
    const byDataset=report?.test_metrics_by_dataset||{},rows=[];
    for(const [dataset,data] of Object.entries(byDataset)) {
      if(data?.ground_truth_available===false||data?.conditional_if_all_nonfight){
        for(const mode of modes){const conditional=data.conditional_if_all_nonfight?.[mode]||{};rows.push(`<tr><td>${escapeHtml(dataset)}</td><td>无标签</td><td>${conditional.n_unlabeled??data.unlabeled_video_count??"—"}</td><td>${names[mode]}</td><td>${conditional.predicted_positive_count??"—"} 条被判 Fight（假设全为 NonFight） · ${pct(conditional.conditional_false_positive_rate_if_all_nonfight)}</td><td colspan="4">不计算真实分类指标</td></tr>`);}
      } else {
        for(const mode of modes){const m=data?.[mode]||{};rows.push(`<tr><td>${escapeHtml(dataset)}</td><td>有标签</td><td>${m.n??"—"}</td><td>${names[mode]}</td><td>${m.tp??"—"}/${m.tn??"—"}/${m.fp??"—"}/${m.fn??"—"}</td><td>${fmt(m.precision,3)}</td><td>${fmt(m.recall,3)}</td><td>${fmt(m.f1,3)}</td><td>${fmt(m.roc_auc,3)}</td></tr>`);}
      }
    }
    $("reverseDatasetTestTable").innerHTML=rows.length?rows.join(""):'<tr><td colspan="9" class="empty-state">最终测试后显示原有数据集 test、UCF test、AIRTLab 与 GuardAId 的分来源结果。</td></tr>';
    $("reverseTestNote").textContent=report?.best_epoch?`最佳 epoch ${report.best_epoch} · 验证 AUC ${fmt(report.best_validation_roc_auc,4)} · 测试视频 ${report.test_video_count??"—"} 条 · 测试窗口 ${report.test_window_count??"—"} 个。阈值由原有五个数据集与 UCF 的合并 validation split 选择。`:"GuardAId 没有真实标签，因此只报告预测数和假设全为 NonFight 时的条件性误报率。";
  }
  function renderReverseStatus(status) {
    const previous=reverseApp.status?.status;reverseApp.status=status;
    const s=status.status||"idle",labels={idle:"空闲",starting:"正在启动",running:"运行中",pause_requested:"待安全暂停",stopping:"正在停止",paused:"已暂停",stopped:"已停止",resumable:"可恢复",completed:"已完成",failed:"运行失败",incomplete:"尚未完成"};
    const active=["starting","running","pause_requested","stopping"].includes(s),p=Math.round(100*(status.stage_progress||0));
    $("reverseStatusText").textContent=labels[s]||s;$("reverseStatusPill").textContent=labels[s]||s;
    $("reverseStatusPill").className=`status-pill ${active?"running":s==="completed"?"completed":["failed","stopped"].includes(s)?s:""}`;
    $("reverseStatusDot").className=`dot ${active?"good":["failed","stopped"].includes(s)?"bad":s==="completed"?"good":""}`;
    $("reverseStageText").textContent=status.stage||"等待启动训练";$("reverseProgressPercent").textContent=`${p}%`;$("reverseProgressFill").style.width=`${p}%`;
    $("reverseEpochText").textContent=status.total_epochs?`Epoch ${status.current_epoch||0} / ${status.total_epochs} · 已完成 ${status.completed_epochs||0} 轮`:"Epoch — / —";
    $("reverseElapsedText").textContent=status.elapsed_seconds===null||status.elapsed_seconds===undefined?"已用时间 —":`已用 ${duration(status.elapsed_seconds)}`;
    $("reverseEtaText").textContent=status.eta_seconds===null||status.eta_seconds===undefined?"预计剩余时间 —":`预计剩余 ${duration(status.eta_seconds)}`;
    const history=status.history||[],latest=status.latest_epoch||{};
    $("reverseMetricLoss").textContent=fmt(latest.train_loss,4);$("reverseMetricAuc").textContent=fmt(latest.val_roc_auc,4);
    const best=status.final_report?.best_validation_roc_auc??Math.max(-Infinity,...history.map(row=>Number(row.val_roc_auc)).filter(Number.isFinite));
    $("reverseMetricBest").textContent=Number.isFinite(best)?fmt(best,4):"—";
    const bestEpoch=status.final_report?.best_epoch??history.reduce((bestRow,row)=>Number(row.val_roc_auc)>Number(bestRow?.val_roc_auc??-Infinity)?row:bestRow,null)?.epoch;
    $("reverseBestEpoch").textContent=bestEpoch?`最佳 epoch ${bestEpoch}`:"最佳 epoch —";
    $("reverseMetricSamples").textContent=status.manifest_count?Number(status.sample_total||0).toLocaleString():"—";
    $("reverseMetricAugmented").textContent=status.manifest_count?Number(status.reverse_concat_sample_total||0).toLocaleString():"—";
    $("reverseMetricExcluded").textContent=status.manifest_count?Number(status.excluded_total||0).toLocaleString():"—";
    $("reversePauseButton").disabled=!active||s==="pause_requested"||s==="stopping";$("reverseCancelPauseButton").hidden=s!=="pause_requested";$("reverseStopButton").disabled=!active;
    $("reverseResumeRun").disabled=active;$("reverseStartButton").disabled=active||!reverseApp.summary?.ready||!app.health?.model_ready;
    if(status.last_checkpoint){$("reverseCheckpointBox").classList.add("show");$("reverseCheckpointPath").textContent=status.last_checkpoint;}
    if(status.run_dir)$("reverseRunPathLabel").textContent=`运行目录：${status.run_dir}`;
    renderReverseHistory(history,bestEpoch);renderReverseEpochTable(history,bestEpoch);renderReverseFinalReport(status.final_report||{});
    const log=status.log_text||status.console?.join("\n")||"";
    if(log!==reverseApp.lastLog){const el=$("reverseConsole"),nearBottom=el.scrollHeight-el.scrollTop-el.clientHeight<70;el.textContent=log||"等待控制台输出。";el.classList.toggle("empty",!log);if(nearBottom)el.scrollTop=el.scrollHeight;reverseApp.lastLog=log;}
    if(s==="failed"&&status.return_code!==null)showBanner("reverseBanner",`倒放拼接训练进程已结束（退出代码 ${status.return_code}）。请查看日志末尾。`,"error");
    if(s==="paused")showBanner("reverseBanner","训练已在完整 epoch 边界安全暂停；可以从此 checkpoint 恢复。","info");
    if(s==="completed"){showBanner("reverseBanner","训练、验证集阈值选择及最终留出评估均已完成，结果已保存在本方案运行目录。","info");if(previous!==s){reverseApp.selectedRunsRoot="";loadReverseRuns(true);}}
    if(s==="stopped"&&previous!==s){reverseApp.selectedRunsRoot="";loadReverseRuns(true);}
    updateReverseControls();
  }
  async function pollReverseStatus() {
    if(reverseApp.polling)return;reverseApp.polling=true;
    try {const root=$("reverseOutputRoot").value.trim();const params=new URLSearchParams();if(root)params.set("output_root",root);if($("reverseHistoryRun").value)params.set("run_dir",$("reverseHistoryRun").value);const query=params.toString()?`?${params.toString()}`:"";const status=await api(`/api/reverse/status${query}`);renderReverseStatus(status);await loadReverseRuns(false);}
    catch(_){ /* the main 1-second page remains available when the reverse API is not ready */ }
    finally {reverseApp.polling=false;}
  }
  window.addEventListener("resize",()=>{reverseApp.lastChartKey="";if(reverseApp.status)renderReverseHistory(reverseApp.status.history||[],reverseApp.status.final_report?.best_epoch);});

  function fourRoots() {
    return {
      legacy_dataset_root:$("fourLegacyRoot").value.trim(),
      ucf_root:$("fourUcfRoot").value.trim(),
      airtlab_root:$("fourAirRoot").value.trim(),
      guardaid_root:$("fourGuardRoot").value.trim(),
      output_root:$("fourOutputRoot").value.trim()
    };
  }
  async function loadFourDatasetSummary() {
    try {
      const query=new URLSearchParams(fourRoots());
      const summary=await api(`/api/four/summary?${query.toString()}`);
      fourApp.summary=summary;
      if(!$("fourLegacyRoot").value) $("fourLegacyRoot").value=summary.roots.legacy;
      if(!$("fourUcfRoot").value) $("fourUcfRoot").value=summary.roots.ucf;
      if(!$("fourAirRoot").value) $("fourAirRoot").value=summary.roots.airtlab;
      if(!$("fourGuardRoot").value) $("fourGuardRoot").value=summary.roots["guardaid"];
      if(!$("fourOutputRoot").value) $("fourOutputRoot").value=summary.roots.output;
      if(!$("fourCacheDir").value) $("fourCacheDir").value=summary.cache?.supplemental||"";
      renderFourDatasetSummary(summary);
      if(!summary.ready) showBanner("fourBanner","数据路径尚未齐全。请确认三个所选数据集目录、UCF 两类目录、外部测试路径，以及既有两秒帧缓存。","warn");
      else if($("fourBanner").classList.contains("warn") && !fourApp.status?.process_active) hideBanner("fourBanner");
      await loadFourRuns(true);
    } catch(err) {
      showBanner("fourBanner",`无法读取数据路径：${err.message}`,"error");
      $("fourSourceSummary").innerHTML='<div class="source-summary"><strong>数据路径检查失败</strong><span>请确认路径存在且本地服务仍在运行。</span></div>';
      fourApp.summary=null;
      updateFourControls();
    }
  }
  function renderFourDatasetSummary(data) {
    const u=data.ucf||{}, a=data.airtlab||{}, g=data["guardaid"]||{}, splits=u.split_preview||{};
    const legacy=data.legacy||{}, legacyDatasets=legacy.datasets||[];
    const fightSplits=["train","val","test"].map(key=>`${({train:"Train",val:"Val",test:"Test"})[key]} ${splits[key]?.Fight??"—"}`).join(" · ");
    const normalSplits=["train","val","test"].map(key=>`${({train:"Train",val:"Val",test:"Test"})[key]} ${splits[key]?.NonFight??"—"}`).join(" · ");
    const archives=(u.normal_archives||[]).reduce((total,row)=>total+Number(row.videos||0),0);
    const legacyCards=legacyDatasets.map(item=>{
      const splitLine=(split,label)=>`${({train:"Train",val:"Val",test:"Test"})[split]} ${label} ${Number(item.splits?.[split]?.[label]||0).toLocaleString()}`;
      return `<div class="source-summary"><strong>${escapeHtml(item.name)} · 保留原 split</strong><span class="source-count">${Number(item.total||0).toLocaleString()} 段</span><span>${splitLine("train","Fight")} / ${splitLine("train","NonFight")}<br>${splitLine("val","Fight")} / ${splitLine("val","NonFight")}<br>${splitLine("test","Fight")} / ${splitLine("test","NonFight")}${item.missing?.length?`<br><span class="text-danger">缺少 ${item.missing.length} 个类别目录</span>`:""}</span></div>`;
    }).join("");
    $("fourSourceSummary").innerHTML=`${legacyCards}
      <div class="source-summary"><strong>UCF · 80/10/10</strong><span class="source-count">${Number(u.raw_fight||0).toLocaleString()} Fight · ${Number(u.raw_nonfight_including_archive||0).toLocaleString()} NonFight</span><span>暴力子类：Assault ${Number(u.assault_videos||0).toLocaleString()} · Fighting ${Number(u.fighting_videos||0).toLocaleString()}。正常视频文件夹 ${Number(u.nonfight_folder_videos||0).toLocaleString()}${archives?` · 压缩包 ${archives.toLocaleString()}`:""}<br>${fightSplits}<br>${normalSplits}</span></div>
      <div class="source-summary"><strong>AIRTLab · 有标签外部测试</strong><span class="source-count">${Number(a.total||0).toLocaleString()} 段</span><span>Fight ${Number(a.fight||0).toLocaleString()} · NonFight ${Number(a.nonfight||0).toLocaleString()}<br>仅在训练后评估，不参与训练、验证或阈值选择。</span></div>
      <div class="source-summary"><strong>GuardAId · 无标签外部测试</strong><span class="source-count">${Number(g.videos||0).toLocaleString()} 段</span><span>没有 ground truth 标签，只保存预测；可另列“假设全为 NonFight”的条件性误报率。</span></div>`;
    const free=data.storage?.free_gib, uncompressed=data.storage?.normal_archive_uncompressed_gib;
    const normalFolder=Number(u.nonfight_folder_videos||0);
    let storageText=`可用空间 ${free===null||free===undefined?"—":`${fmt(free,1)} GiB`}`;
    if(normalFolder) storageText+=` · 已识别 Non-Violence 文件夹中的 ${normalFolder.toLocaleString()} 段视频`;
    else if(Number(u.normal_archive_videos||0)) storageText+=` · 程序逐条临时读取压缩包，无需完整解压${uncompressed?`（整包解压约 ${fmt(uncompressed,1)} GiB）`:""}`;
    const cache=data.cache||{};
    storageText+=" · 两秒缓存只读 "+fmt(cache.legacy_gib,2)+" GiB · 补充缓存 "+fmt(cache.supplemental_gib,2)+" / 5.00 GiB";
    $("fourStorageNote").textContent=storageText;
    updateFourControls();
  }
  function updateFourControls() {
    const status=fourApp.status?.status||"idle";
    const active=Boolean(fourApp.status?.process_active)||["starting","running","pause_requested","stopping"].includes(status);
    const resumeSelected=Boolean($("fourResumeRun")?.value);
    $("fourStartButton").disabled=active||!fourApp.summary?.ready||!app.health?.model_ready;
    $("fourPauseButton").disabled=!active||status==="pause_requested"||status==="stopping";
    $("fourCancelPauseButton").hidden=status!=="pause_requested";
    $("fourStopButton").disabled=!active;
    $("fourResumeRun").disabled=active;
    $("fourHistoryRun").disabled=active;
    $("fourStartButton").textContent=resumeSelected?"从 checkpoint 恢复训练":"开始 4 秒方案训练";
    const locked=resumeSelected;
    ["fourLegacyRoot","fourUcfRoot","fourAirRoot","fourGuardRoot","fourOutputRoot","fourEpochs","fourPatience","fourMinDelta","fourBackboneLr","fourHeadLr","fourWeightDecay","fourGradAccum","fourWindowBatchSize","fourSeed","fourDevice","fourCacheDir","fourAmp","fourAllowCpu"].forEach(id=>$(id).disabled=active||locked);
  }

  async function loadFourRuns(force=false) {
    const outputRoot=$("fourOutputRoot").value.trim()||fourApp.summary?.roots?.output||"";
    if(!outputRoot)return;
    if(!force&&fourApp.selectedRunsRoot===outputRoot&&fourApp.lastRunsLoad&&Date.now()-fourApp.lastRunsLoad<10000)return;
    fourApp.selectedRunsRoot=outputRoot;fourApp.lastRunsLoad=Date.now();
    try {
      const {runs}=await api(`/api/four/runs?output_root=${encodeURIComponent(outputRoot)}`);
      fourApp.runs=runs||[];
      const historySelect=$("fourHistoryRun"),oldHistory=historySelect.value;
      historySelect.innerHTML=fourApp.runs.length?"":'<option value="">尚无四秒窗口运行记录</option>';
      fourApp.runs.forEach(run=>{
        const option=document.createElement("option");option.value=run.run_dir;
        const result=run.best_validation_roc_auc===null||run.best_validation_roc_auc===undefined?"":` · 最佳 AUC ${fmt(run.best_validation_roc_auc,3)}`;
        option.textContent=`${run.name} · ${run.status}${result}`;historySelect.appendChild(option);
      });
      if([...historySelect.options].some(option=>option.value===oldHistory))historySelect.value=oldHistory;
      else if(fourApp.runs.length)historySelect.value=fourApp.runs[0].run_dir;
      const select=$("fourResumeRun"),old=select.value;
      select.innerHTML='<option value="">新建运行</option>';
      fourApp.runs.filter(run=>run.last_checkpoint&&run.status!=="completed").forEach(run=>{
        const option=document.createElement("option");option.value=run.last_checkpoint;
        option.textContent=`${run.name} · ${run.status==="resumable"?"可恢复":"未完成"}`;select.appendChild(option);
      });
      if([...select.options].some(option=>option.value===old))select.value=old;
      updateFourControls();
      if(historySelect.value&&historySelect.value!==oldHistory)pollFourStatus();
      if(select.value)await loadFourResumeConfig();
    } catch(err) { /* keep the last visible history if the output folder is temporarily unavailable */ }
  }
  async function loadFourResumeConfig() {
    const checkpoint=$("fourResumeRun").value;
    if(!checkpoint){fourApp.configCheckpoint="";updateFourControls();return;}
    if(fourApp.configCheckpoint===checkpoint){updateFourControls();return;}
    const root=$("fourOutputRoot").value.trim()||fourApp.summary?.roots?.output||"";
    const runDir=checkpoint.replace(/[\\/][^\\/]+$/,"" );
    const {config}=await api(`/api/four/run-config?run_dir=${encodeURIComponent(runDir)}&output_root=${encodeURIComponent(root)}`);
    const roots=config.preprocessing?.source_roots||{};
    $("fourLegacyRoot").value=roots.legacy||fourApp.summary?.roots?.legacy||$("fourLegacyRoot").value;
    $("fourUcfRoot").value=roots.ucf||$("fourUcfRoot").value;
    $("fourAirRoot").value=roots.airtlab||$("fourAirRoot").value;
    $("fourGuardRoot").value=roots["guardaid"]||$("fourGuardRoot").value;
    $("fourOutputRoot").value=root;
    $("fourEpochs").value=config.epochs??30;$("fourPatience").value=config.patience??6;$("fourMinDelta").value=config.min_delta??0.001;
    $("fourBackboneLr").value=config.backbone_lr??0.00002;$("fourHeadLr").value=config.head_lr??0.0001;$("fourWeightDecay").value=config.weight_decay??0.0001;
    $("fourGradAccum").value=config.grad_accum??8;$("fourWindowBatchSize").value=config.window_batch_size??4;$("fourSeed").value=config.seed??2026;
    const device=String(config.device||"auto");$("fourDevice").value=device.startsWith("cpu")?"cpu":device.startsWith("cuda")?"cuda":"auto";
    $("fourAmp").checked=Boolean(config.amp);$("fourAllowCpu").checked=$("fourDevice").value==="cpu";
    $("fourCacheDir").value=config.cache_dir||"";
    fourApp.configCheckpoint=checkpoint;updateFourControls();
    showBanner("fourBanner","已载入所选运行的训练配置和数据来源；恢复时沿用原 checkpoint 设置。","info");
  }
  function collectFourConfig() {
    return {
      ...fourRoots(),resume_checkpoint:$("fourResumeRun").value,
      epochs:$("fourEpochs").value,patience:$("fourPatience").value,min_delta:$("fourMinDelta").value,
      backbone_lr:$("fourBackboneLr").value,head_lr:$("fourHeadLr").value,weight_decay:$("fourWeightDecay").value,
      grad_accum:$("fourGradAccum").value,window_batch_size:$("fourWindowBatchSize").value,seed:$("fourSeed").value,
      device:$("fourDevice").value,cache_dir:$("fourCacheDir").value.trim(),amp:$("fourAmp").checked,allow_cpu:$("fourAllowCpu").checked
    };
  }
  $("fourResumeRun").addEventListener("change",()=>{fourApp.configCheckpoint="";loadFourResumeConfig().catch(err=>showBanner("fourBanner",err.message,"error"));updateFourControls();});
  $("fourHistoryRun").addEventListener("change",()=>pollFourStatus());
  ["fourLegacyRoot","fourUcfRoot","fourAirRoot","fourGuardRoot"].forEach(id=>$(id).addEventListener("change",()=>loadFourDatasetSummary()));
  $("fourOutputRoot").addEventListener("change",()=>{fourApp.selectedRunsRoot="";fourApp.configCheckpoint="";loadFourDatasetSummary();loadFourRuns(true);pollFourStatus();});
  $("fourRefreshDataButton").addEventListener("click",()=>loadFourDatasetSummary());
  $("fourRefreshRunsButton").addEventListener("click",()=>loadFourRuns(true));
  $("fourStartButton").addEventListener("click",async()=>{
    const button=$("fourStartButton");button.disabled=true;hideBanner("fourBanner");
    try {
      const result=await postJson("/api/four/training/start",collectFourConfig());
      showBanner("fourBanner",result.resuming?"四秒窗口训练已恢复，将从 checkpoint 的下一个完整 epoch 继续。":"四秒窗口训练已启动，正在建立视频清单并准备帧缓存。","info");
      $("fourRunPathLabel").textContent=`运行目录：${result.run_dir}`;
      if(![...$("fourHistoryRun").options].some(option=>option.value===result.run_dir)){const option=document.createElement("option");option.value=result.run_dir;option.textContent=result.run_dir.split(/[\\/]/).pop()+" · running";$("fourHistoryRun").appendChild(option);}
      $("fourHistoryRun").value=result.run_dir;
      fourApp.selectedRunsRoot="";await pollFourStatus();
    } catch(err) { showBanner("fourBanner",err.message,"error"); }
    finally { updateFourControls(); }
  });
  $("fourPauseButton").addEventListener("click",async()=>{try{const result=await postJson("/api/four/training/pause");showBanner("fourBanner",result.message,"info");await pollFourStatus();}catch(err){showBanner("fourBanner",err.message,"error");}});
  $("fourCancelPauseButton").addEventListener("click",async()=>{try{const result=await postJson("/api/four/training/cancel-pause");showBanner("fourBanner",result.message,"info");await pollFourStatus();}catch(err){showBanner("fourBanner",err.message,"error");}});
  $("fourStopButton").addEventListener("click",async()=>{
    if(!window.confirm("立即停止会中断当前 epoch；最近一次完整 checkpoint 可供恢复。确定停止吗？"))return;
    try{const result=await postJson("/api/four/training/stop");showBanner("fourBanner",result.message,"warn");await pollFourStatus();}catch(err){showBanner("fourBanner",err.message,"error");}
  });
  $("fourCopyRunButton").addEventListener("click",async()=>{
    const path=fourApp.status?.run_dir;if(!path)return;
    try{await navigator.clipboard.writeText(path);$("fourCopyRunButton").textContent="已复制";setTimeout(()=>$("fourCopyRunButton").textContent="复制运行路径",1400);}
    catch(_){showBanner("fourBanner",`运行目录：${path}`,"info");}
  });
  function renderFourEpochTable(history,bestEpoch) {
    const body=$("fourEpochTable");
    if(!history.length){body.innerHTML='<tr><td colspan="7" class="empty-state">完成第一个 epoch 后显示记录。</td></tr>';return;}
    body.innerHTML=[...history].reverse().map(row=>`<tr><td>${row.epoch??"—"}</td><td>${fmt(row.train_loss,4)}</td><td>${fmt(row.train_roc_auc,3)}</td><td>${fmt(row.val_roc_auc,4)}</td><td>${fmt(row.val_f1_at_0_5,3)}</td><td>${fmt(row.seconds,1)} s</td><td>${Number(row.epoch)===Number(bestEpoch)?'<span class="tag protocol">最佳</span>':Number(row.improved)===1?"提升":"—"}</td></tr>`).join("");
  }
  function renderFourHistory(history,bestEpoch) {
    $("fourHistoryCount").textContent=`${history.length} 个 epoch${bestEpoch?` · 最佳 ${bestEpoch}`:""}`;
    const key=history.map(row=>`${row.epoch}:${row.train_loss}:${row.val_roc_auc}`).join("|");
    if(key===fourApp.lastChartKey)return;fourApp.lastChartKey=key;
    drawLineChart($("fourLossChart"),history.map(row=>row.train_loss===null?NaN:Number(row.train_loss)),"#087f78",false);
    drawLineChart($("fourAucChart"),history.map(row=>row.val_roc_auc===null?NaN:Number(row.val_roc_auc)),"#328e67",true);
  }
  function renderFourFinalReport(report) {
    const thresholds=report?.thresholds||{},validation=report?.validation_metrics||{},testing=report?.test_metrics||{};
    const modes=["sensitive","moderate","certain"],names={sensitive:"Sensitive",moderate:"Moderate",certain:"Certain"};
    if(!Object.keys(thresholds).length){$("fourThresholdTable").innerHTML='<tr><td colspan="13" class="empty-state">完成训练及最终评估后显示。</td></tr>';}
    else $("fourThresholdTable").innerHTML=modes.map(mode=>{
      const t=thresholds[mode]||{},v=validation[mode]||{},m=testing[mode]||{};
      return `<tr><td>${names[mode]}</td><td>${escapeHtml(t.objective||"—")}</td><td>${fmt(t.threshold,4)}</td><td>${fmt(t.validation_score,3)}</td><td>${fmt(v.precision,3)}</td><td>${fmt(v.recall,3)}</td><td>${fmt(v.f1,3)}</td><td>${fmt(v.roc_auc,3)}</td><td>${m.n??"—"}</td><td>${fmt(m.precision,3)}</td><td>${fmt(m.recall,3)}</td><td>${fmt(m.f1,3)}</td><td>${fmt(m.roc_auc,3)}</td></tr>`;
    }).join("");
    const byDataset=report?.test_metrics_by_dataset||{},rows=[];
    for(const [dataset,data] of Object.entries(byDataset)) {
      if(data?.ground_truth_available===false||data?.conditional_if_all_nonfight){
        for(const mode of modes){const conditional=data.conditional_if_all_nonfight?.[mode]||{};rows.push(`<tr><td>${escapeHtml(dataset)}</td><td>无标签</td><td>${conditional.n_unlabeled??data.unlabeled_video_count??"—"}</td><td>${names[mode]}</td><td>${conditional.predicted_positive_count??"—"} 条被判 Fight（假设全为 NonFight） · ${pct(conditional.conditional_false_positive_rate_if_all_nonfight)}</td><td colspan="4">不计算真实分类指标</td></tr>`);}
      } else {
        for(const mode of modes){const m=data?.[mode]||{};rows.push(`<tr><td>${escapeHtml(dataset)}</td><td>有标签</td><td>${m.n??"—"}</td><td>${names[mode]}</td><td>${m.tp??"—"}/${m.tn??"—"}/${m.fp??"—"}/${m.fn??"—"}</td><td>${fmt(m.precision,3)}</td><td>${fmt(m.recall,3)}</td><td>${fmt(m.f1,3)}</td><td>${fmt(m.roc_auc,3)}</td></tr>`);}
      }
    }
    $("fourDatasetTestTable").innerHTML=rows.length?rows.join(""):'<tr><td colspan="9" class="empty-state">最终测试后显示原有数据集 test、UCF test、AIRTLab 与 GuardAId 的分来源结果。</td></tr>';
    $("fourTestNote").textContent=report?.best_epoch?`最佳 epoch ${report.best_epoch} · 验证 AUC ${fmt(report.best_validation_roc_auc,4)} · 测试视频 ${report.test_video_count??"—"} 条 · 测试窗口 ${report.test_window_count??"—"} 个。阈值由原有五个数据集与 UCF 的合并 validation split 选择。`:"GuardAId 没有真实标签，因此只报告预测数和假设全为 NonFight 时的条件性误报率。";
  }
  function renderFourStatus(status) {
    const previous=fourApp.status?.status;fourApp.status=status;
    const s=status.status||"idle",labels={idle:"空闲",starting:"正在启动",running:"运行中",pause_requested:"待安全暂停",stopping:"正在停止",paused:"已暂停",stopped:"已停止",resumable:"可恢复",completed:"已完成",failed:"运行失败",incomplete:"尚未完成"};
    const active=["starting","running","pause_requested","stopping"].includes(s),p=Math.round(100*(status.stage_progress||0));
    $("fourStatusText").textContent=labels[s]||s;$("fourStatusPill").textContent=labels[s]||s;
    $("fourStatusPill").className=`status-pill ${active?"running":s==="completed"?"completed":["failed","stopped"].includes(s)?s:""}`;
    $("fourStatusDot").className=`dot ${active?"good":["failed","stopped"].includes(s)?"bad":s==="completed"?"good":""}`;
    $("fourStageText").textContent=status.stage||"等待启动训练";$("fourProgressPercent").textContent=`${p}%`;$("fourProgressFill").style.width=`${p}%`;
    $("fourEpochText").textContent=status.total_epochs?`Epoch ${status.current_epoch||0} / ${status.total_epochs} · 已完成 ${status.completed_epochs||0} 轮`:"Epoch — / —";
    $("fourElapsedText").textContent=status.elapsed_seconds===null||status.elapsed_seconds===undefined?"已用时间 —":`已用 ${duration(status.elapsed_seconds)}`;
    $("fourEtaText").textContent=status.eta_seconds===null||status.eta_seconds===undefined?"预计剩余时间 —":`预计剩余 ${duration(status.eta_seconds)}`;
    const history=status.history||[],latest=status.latest_epoch||{};
    $("fourMetricLoss").textContent=fmt(latest.train_loss,4);$("fourMetricAuc").textContent=fmt(latest.val_roc_auc,4);
    const best=status.final_report?.best_validation_roc_auc??Math.max(-Infinity,...history.map(row=>Number(row.val_roc_auc)).filter(Number.isFinite));
    $("fourMetricBest").textContent=Number.isFinite(best)?fmt(best,4):"—";
    const bestEpoch=status.final_report?.best_epoch??history.reduce((bestRow,row)=>Number(row.val_roc_auc)>Number(bestRow?.val_roc_auc??-Infinity)?row:bestRow,null)?.epoch;
    $("fourBestEpoch").textContent=bestEpoch?`最佳 epoch ${bestEpoch}`:"最佳 epoch —";
    $("fourMetricSamples").textContent=status.manifest_count?Number(status.sample_total||0).toLocaleString():"—";
    $("fourMetricAugmented").textContent=status.manifest_count?Number(status.short_tail_sample_total||0).toLocaleString():"—";
    $("fourMetricExcluded").textContent=status.manifest_count?Number(status.excluded_total||0).toLocaleString():"—";
    $("fourPauseButton").disabled=!active||s==="pause_requested"||s==="stopping";$("fourCancelPauseButton").hidden=s!=="pause_requested";$("fourStopButton").disabled=!active;
    $("fourResumeRun").disabled=active;$("fourStartButton").disabled=active||!fourApp.summary?.ready||!app.health?.model_ready;
    if(status.last_checkpoint){$("fourCheckpointBox").classList.add("show");$("fourCheckpointPath").textContent=status.last_checkpoint;}
    if(status.run_dir)$("fourRunPathLabel").textContent=`运行目录：${status.run_dir}`;
    renderFourHistory(history,bestEpoch);renderFourEpochTable(history,bestEpoch);renderFourFinalReport(status.final_report||{});
    const log=status.log_text||status.console?.join("\n")||"";
    if(log!==fourApp.lastLog){const el=$("fourConsole"),nearBottom=el.scrollHeight-el.scrollTop-el.clientHeight<70;el.textContent=log||"等待控制台输出。";el.classList.toggle("empty",!log);if(nearBottom)el.scrollTop=el.scrollHeight;fourApp.lastLog=log;}
    if(s==="failed"&&status.return_code!==null)showBanner("fourBanner",`四秒窗口训练进程已结束（退出代码 ${status.return_code}）。请查看日志末尾。`,"error");
    if(s==="paused")showBanner("fourBanner","训练已在完整 epoch 边界安全暂停；可以从此 checkpoint 恢复。","info");
    if(s==="completed"){showBanner("fourBanner","训练、验证集阈值选择及最终留出评估均已完成，结果已保存在本方案运行目录。","info");if(previous!==s){fourApp.selectedRunsRoot="";loadFourRuns(true);}}
    if(s==="stopped"&&previous!==s){fourApp.selectedRunsRoot="";loadFourRuns(true);}
    updateFourControls();
  }
  async function pollFourStatus() {
    if(fourApp.polling)return;fourApp.polling=true;
    try {const root=$("fourOutputRoot").value.trim();const params=new URLSearchParams();if(root)params.set("output_root",root);if($("fourHistoryRun").value)params.set("run_dir",$("fourHistoryRun").value);const query=params.toString()?`?${params.toString()}`:"";const status=await api(`/api/four/status${query}`);renderFourStatus(status);await loadFourRuns(false);}
    catch(_){ /* the main 1-second page remains available when the four API is not ready */ }
    finally {fourApp.polling=false;}
  }
  window.addEventListener("resize",()=>{fourApp.lastChartKey="";if(fourApp.status)renderFourHistory(fourApp.status.history||[],fourApp.status.final_report?.best_epoch);});

  function collectTrainingConfig() {
    return {
      dataset_root:$("datasetRoot").value.trim(), output_root:$("outputRoot").value.trim(), cache_dir:$("cacheDir").value.trim(),
      resume_checkpoint:$("resumeRun").value, epochs:$("epochs").value, patience:$("patience").value, min_delta:$("minDelta").value,
      backbone_lr:$("backboneLr").value, head_lr:$("headLr").value, weight_decay:$("weightDecay").value,
      grad_accum:$("gradAccum").value, window_batch_size:$("windowBatchSize").value, seed:$("seed").value,
      device:$("device").value, amp:$("amp").checked, allow_cpu:$("allowCpu").checked
    };
  }
  async function postJson(path, payload={}) { return api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}); }
  $("startButton").addEventListener("click",async()=>{
    const button=$("startButton"); button.disabled=true; hideBanner("trainBanner");
    try {
      const result=await postJson("/api/training/start",collectTrainingConfig());
      showBanner("trainBanner",result.resuming?"训练已恢复，将从下一个完整 epoch 继续。":"训练已启动。正在检查数据并准备训练。","info");
      $("runPathLabel").textContent=`运行目录：${result.run_dir}`;
      app.lastRunsLoad=0;
    } catch(err) { showBanner("trainBanner",err.message,"error"); }
    finally { button.disabled=false; await pollStatus(); }
  });
  $("pauseButton").addEventListener("click",async()=>{try{const r=await postJson("/api/training/pause");showBanner("trainBanner",r.message,"info");await pollStatus();}catch(e){showBanner("trainBanner",e.message,"error");}});
  $("cancelPauseButton").addEventListener("click",async()=>{try{const r=await postJson("/api/training/cancel-pause");showBanner("trainBanner",r.message,"info");await pollStatus();}catch(e){showBanner("trainBanner",e.message,"error");}});
  $("stopButton").addEventListener("click",async()=>{
    if(!window.confirm("立即停止会中断当前 epoch；只保留最近一个已完成 epoch 的 checkpoint。确定停止吗？")) return;
    try{const r=await postJson("/api/training/stop");showBanner("trainBanner",r.message,"warn");await pollStatus();}catch(e){showBanner("trainBanner",e.message,"error");}
  });
  $("copyRunButton").addEventListener("click",async()=>{
    const path=app.status?.run_dir; if(!path) return;
    try{await navigator.clipboard.writeText(path);$("copyRunButton").textContent="已复制";setTimeout(()=>$("copyRunButton").textContent="复制路径",1400);}catch(_){showBanner("trainBanner",`运行目录：${path}`,"info");}
  });

  function renderStatus(status) {
    const previousStatus=app.status?.status;
    app.status=status;
    const s=status.status||"idle";
    const labels={idle:"空闲",starting:"正在启动",running:"运行中",pause_requested:"待安全暂停",stopping:"正在停止",paused:"已暂停",stopped:"已停止",completed:"已完成",failed:"运行失败"};
    $("trainStatusText").textContent=labels[s]||"准备中";
    $("trainStatusPill").textContent=labels[s]||s;
    $("trainStatusPill").className=`status-pill ${["running","pause_requested"].includes(s)?"running":["completed"].includes(s)?"completed":["failed","stopped"].includes(s)?s:""}`;
    $("trainStatusDot").className=`dot ${["running","pause_requested"].includes(s)?"good":["failed","stopped"].includes(s)?"bad":s==="completed"?"good":""}`;
    const p=Math.round(100*(status.stage_progress||0));
    $("stageText").textContent=status.stage||"等待启动训练"; $("progressPercent").textContent=`${p}%`; $("progressFill").style.width=`${p}%`;
    $("epochText").textContent=status.total_epochs?`Epoch ${status.current_epoch||0} / ${status.total_epochs} · 已完成 ${status.completed_epochs||0} 轮`:"Epoch — / —";
    $("etaText").textContent=status.eta_seconds!==null&&status.eta_seconds!==undefined&&Number.isFinite(Number(status.eta_seconds))?`预计本阶段剩余 ${duration(status.eta_seconds)}`:"预计剩余时间 —";
    const latest=status.latest_epoch||{}; $("metricLoss").textContent=fmt(latest.train_loss,4); $("metricAuc").textContent=fmt(latest.val_roc_auc,4); $("metricF1").textContent=fmt(latest.val_f1_at_0_5,4);
    const best=status.final_report?.best_validation_roc_auc ?? Math.max(-Infinity,...(status.history||[]).map(row=>Number(row.val_roc_auc)).filter(Number.isFinite));
    $("metricBest").textContent=Number.isFinite(best)?fmt(best,4):"—";
    const bestEpoch=status.final_report?.best_epoch ?? ((status.history||[]).reduce((bestRow,row)=>Number(row.val_roc_auc)>Number(bestRow?.val_roc_auc??-Infinity)?row:bestRow,null)?.epoch);
    $("bestEpochDetail").textContent=bestEpoch?`最佳 epoch ${bestEpoch}`:"最佳 epoch —";
    $("metricSamples").textContent=status.manifest_count?Number(status.sample_total||0).toLocaleString():"—";
    $("metricExcluded").textContent=status.manifest_count?Number(status.excluded_total||0).toLocaleString():"—";
    const active=["starting","running","pause_requested","stopping"].includes(s);
    $("pauseButton").disabled=!active || s==="pause_requested" || s==="stopping";
    $("cancelPauseButton").hidden=s!=="pause_requested";
    $("stopButton").disabled=!active;
    $("startButton").disabled=active;
    if(status.last_checkpoint) { $("resumeBox").classList.add("show"); $("checkpointPath").textContent=status.last_checkpoint; }
    if(status.run_dir) $("runPathLabel").textContent=`运行目录：${status.run_dir}`;
    renderHistory(status.history||[]); renderEpochTable(status.history||[],bestEpoch); renderValidationTable(status.validation_by_dataset||[]); renderFinalReport(status.final_report||{});
    const log=status.log_text||"";
    if(log!==app.lastLog) { const el=$("consoleLog"); const nearBottom=el.scrollHeight-el.scrollTop-el.clientHeight<70; el.textContent=log||"等待控制台输出。"; el.classList.toggle("empty",!log); if(nearBottom) el.scrollTop=el.scrollHeight; app.lastLog=log; }
    if(s==="failed" && status.return_code!==null) showBanner("trainBanner",`训练进程已结束（退出代码 ${status.return_code}）。请查看控制台日志末尾了解原因。`,`error`);
    if(s==="paused") { showBanner("trainBanner","训练已在 epoch 边界安全暂停。选择“继续训练”可从此 checkpoint 恢复。","info"); if(previousStatus!==s)app.lastRunsLoad=0; }
    if(s==="completed") { showBanner("trainBanner","训练、验证阈值校准与最终测试均已完成。模型和报告已保存到运行目录。","info"); if(previousStatus!==s)app.lastRunsLoad=0; }
    if(s==="stopped"&&previousStatus!==s)app.lastRunsLoad=0;
  }
  function duration(seconds) { const n=Math.max(0,Math.round(Number(seconds)||0)); if(n<60)return `${n} 秒`;const m=Math.floor(n/60),s=n%60; if(m<60)return `${m} 分 ${s} 秒`;return `${Math.floor(m/60)} 小时 ${m%60} 分`; }
  async function pollStatus() { if(app.polling)return;app.polling=true;try { const status=await api("/api/status"); renderStatus(status); await loadRuns(false); } catch(err) { /* transient refresh error */ } finally { app.polling=false; } }
  function renderHistory(history) {
    $("historyCount").textContent=`${history.length} 个 epoch`;
    const key=history.map(row=>`${row.epoch}:${row.train_loss}:${row.val_roc_auc}`).join("|");
    if(key===app.lastChartKey)return; app.lastChartKey=key;
    drawLineChart($("lossChart"),history.map(row=>Number(row.train_loss)),"#087f78",false);
    drawLineChart($("aucChart"),history.map(row=>Number(row.val_roc_auc)),"#328e67",true);
  }
  function drawLineChart(canvas, raw, color, clampAuc) {
    const values=raw.filter(Number.isFinite); const rect=canvas.getBoundingClientRect(); const scale=window.devicePixelRatio||1;
    const width=Math.max(260,rect.width),height=112; canvas.width=Math.floor(width*scale); canvas.height=Math.floor(height*scale);
    const ctx=canvas.getContext("2d"); ctx.scale(scale,scale); ctx.clearRect(0,0,width,height);
    const pad={l:31,r:8,t:9,b:19},plotW=width-pad.l-pad.r,plotH=height-pad.t-pad.b;
    let min,max;
    if(clampAuc){min=0;max=1;} else if(values.length){min=Math.min(...values);max=Math.max(...values);const span=max-min||Math.max(.08,Math.abs(max)*.1);min=Math.max(0,min-span*.17);max=max+span*.17;} else {min=0;max=1;}
    ctx.font="9px system-ui"; ctx.lineWidth=1; ctx.strokeStyle="#eaf0ed"; ctx.fillStyle="#8c9a98";
    for(let i=0;i<3;i++){const y=pad.t+plotH*i/2;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(width-pad.r,y);ctx.stroke();const label=max-(max-min)*i/2;ctx.fillText(label.toFixed(clampAuc?1:2),2,y+3);}
    if(values.length===0){ctx.fillStyle="#91a09e";ctx.fillText("训练后显示曲线",pad.l+10,height/2);return;}
    ctx.strokeStyle=color;ctx.lineWidth=2;ctx.lineJoin="round";ctx.lineCap="round";ctx.beginPath();
    values.forEach((value,index)=>{const x=pad.l+(values.length===1?plotW/2:plotW*index/(values.length-1));const y=pad.t+plotH*(max-value)/(max-min||1);if(index===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();
    values.forEach((value,index)=>{const x=pad.l+(values.length===1?plotW/2:plotW*index/(values.length-1));const y=pad.t+plotH*(max-value)/(max-min||1);ctx.beginPath();ctx.fillStyle="#fff";ctx.arc(x,y,3,0,Math.PI*2);ctx.fill();ctx.strokeStyle=color;ctx.lineWidth=1.7;ctx.stroke();});
    ctx.fillStyle="#8c9a98";ctx.fillText("1",pad.l,height-3);ctx.fillText(String(values.length),Math.max(pad.l,width-pad.r-7),height-3);
  }
  window.addEventListener("resize",()=>{app.lastChartKey="";if(app.status)renderHistory(app.status.history||[]);});
  function renderValidationTable(rows) {
    const body=$("validationTable");
    if(!rows.length){body.innerHTML='<tr><td colspan="6" class="empty-state">完成第一轮验证后显示分项结果。</td></tr>';return;}
    body.innerHTML=rows.map(row=>`<tr><td>${escapeHtml(row.dataset)}</td><td>${row.n??"—"}</td><td>${fmt(row.roc_auc,3)}</td><td>${fmt(row.f1,3)}</td><td>${fmt(row.precision,3)}</td><td>${fmt(row.recall,3)}</td></tr>`).join("");
  }
  function renderEpochTable(history,bestEpoch) {
    const body=$("epochTable");
    if(!history.length){body.innerHTML='<tr><td colspan="10" class="empty-state">完成第一个 epoch 后显示记录。</td></tr>';return;}
    body.innerHTML=[...history].reverse().map(row=>{
      const isBest=Number(row.epoch)===Number(bestEpoch);
      return `<tr><td>${row.epoch??"—"}</td><td>${fmt(row.train_loss,4)}</td><td>${fmt(row.train_roc_auc,3)}</td><td>${fmt(row.val_roc_auc,4)}</td><td>${fmt(row.val_accuracy_at_0_5,3)}</td><td>${fmt(row.val_f1_at_0_5,3)}</td><td>${fmt(row.learning_rate_backbone,2)}</td><td>${fmt(row.learning_rate_head,2)}</td><td>${fmt(row.seconds,1)} s</td><td>${isBest?'<span class="tag protocol">最佳</span>':Number(row.improved)===1?"提升":"—"}</td></tr>`;
    }).join("");
  }
  function renderFinalReport(report) {
    const card=$("finalResultsCard"); if(!report || !report.test_metrics){card.hidden=true;return;} card.hidden=false;
    const modeNames={sensitive:"Sensitive",moderate:"Moderate",certain:"Certain"}; const metrics=report.test_metrics,thresholds=report.thresholds||{};
    $("testTable").innerHTML=["sensitive","moderate","certain"].map(mode=>{
      const m=metrics[mode]||{},t=thresholds[mode]||{};
      return `<tr><td>${modeNames[mode]}</td><td>${fmt(t.threshold,4)}</td><td>${fmt(t.validation_score,3)} ${escapeHtml(t.objective||"")}</td><td>${m.n??"—"}</td><td>${m.tp??"—"}/${m.tn??"—"}/${m.fp??"—"}/${m.fn??"—"}</td><td>${fmt(m.accuracy,3)}</td><td>${fmt(m.precision,3)}</td><td>${fmt(m.recall,3)}</td><td>${fmt(m.specificity,3)}</td><td>${fmt(m.balanced_accuracy,3)}</td><td>${fmt(m.f0_5,3)}</td><td>${fmt(m.f1,3)}</td><td>${fmt(m.f2,3)}</td><td>${fmt(m.roc_auc,3)}</td><td>${fmt(m.average_precision,3)}</td></tr>`;
    }).join("");
    $("testSummary").textContent=`最佳 Epoch ${report.best_epoch??"—"} · 验证 ROC-AUC ${fmt(report.best_validation_roc_auc,4)} · 测试视频 ${report.test_video_count??"—"} 条 · 测试窗口 ${report.test_window_count??"—"} 个 · 推理 ${fmt(report.test_seconds_per_video,3)} 秒/视频`;
    const mode=$("datasetTestMode").value||"moderate";
    const byDataset=report.test_metrics_by_dataset||{};
    const rows=Object.entries(byDataset).sort(([a],[b])=>a.localeCompare(b)).map(([dataset,byMode])=>{
      const m=byMode?.[mode]||{};
      return `<tr><td>${escapeHtml(dataset)}</td><td>${m.n??"—"}</td><td>${m.tp??"—"}/${m.tn??"—"}/${m.fp??"—"}/${m.fn??"—"}</td><td>${fmt(m.accuracy,3)}</td><td>${fmt(m.precision,3)}</td><td>${fmt(m.recall,3)}</td><td>${fmt(m.specificity,3)}</td><td>${fmt(m.f1,3)}</td><td>${fmt(m.roc_auc,3)}</td></tr>`;
    });
    $("datasetTestTable").innerHTML=rows.length?rows.join(""):'<tr><td colspan="9" class="empty-state">没有逐数据集测试数据。</td></tr>';
  }
  $("datasetTestMode").addEventListener("change",()=>{if(app.status?.final_report)renderFinalReport(app.status.final_report);});

  $("inferMode").addEventListener("change",()=>$("modeHelp").textContent=modeDescriptions[$("inferMode").value]);
  const fileInput=$("videoFile"),drop=$("dropZone"),videoPreview=$("videoPreview"),videoStage=$("videoStage"),videoCanvas=$("videoCanvas"),liveScoreOverlay=$("liveScoreOverlay");
  const videoAudioButton=$("videoAudioButton");
  let alertAudioContext=null,alertLatched=false;
  function setAudioStatus(message,warning=false) {
    $("audioStatus").textContent=message;
    $("audioStatus").classList.toggle("warning",warning);
  }
  async function unlockAlertAudio() {
    const AudioContextClass=window.AudioContext||window.webkitAudioContext;
    if(!AudioContextClass)throw new Error("此浏览器不支持提示音");
    if(!alertAudioContext)alertAudioContext=new AudioContextClass();
    if(alertAudioContext.state==="suspended")await alertAudioContext.resume();
  }
  function playAlertBeep() {
    if(!alertAudioContext||alertAudioContext.state!=="running")return;
    const now=alertAudioContext.currentTime,oscillator=alertAudioContext.createOscillator(),gain=alertAudioContext.createGain();
    oscillator.type="sine";oscillator.frequency.value=880;
    gain.gain.setValueAtTime(0.0001,now);
    gain.gain.exponentialRampToValueAtTime(0.5,now+0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001,now+0.22);
    oscillator.connect(gain);gain.connect(alertAudioContext.destination);
    oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};
    oscillator.start(now);oscillator.stop(now+0.24);
  }
  videoStage.addEventListener("pointerdown",()=>unlockAlertAudio().catch(()=>{}),{capture:true});
  videoStage.addEventListener("keydown",()=>unlockAlertAudio().catch(()=>{}),{capture:true});
  $("alertBeepTest").addEventListener("click",async()=>{
    try{await unlockAlertAudio();playAlertBeep();}
    catch(_){setAudioStatus("浏览器阻止了音频输出，请允许此页面播放声音。",true);}
  });
  $("alertBeepEnabled").addEventListener("change",()=>unlockAlertAudio().catch(()=>{}));
  async function releaseBrowserPreview(jobId) {
    if(jobId)await api("/api/media/release",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({job_id:jobId})}).catch(()=>{});
  }
  async function prepareBrowserPreview(file,selectionId) {
    setAudioStatus("正在准备兼容预览与音轨…");
    let jobId=null;
    try {
      const created=await api("/api/media/prepare?filename="+encodeURIComponent(file.name),{method:"POST",headers:{"Content-Type":"application/octet-stream"},body:file});
      jobId=created.job_id;
      if(selectionId!==app.videoSelectionId){await releaseBrowserPreview(jobId);return;}
      app.mediaJob=jobId;
      while(selectionId===app.videoSelectionId){
        const state=await api("/api/media/status?id="+encodeURIComponent(jobId));
        if(state.status==="failed")throw new Error(state.error||"兼容预览准备失败");
        if(state.status==="completed"){
          const position=videoPreview.currentTime,wasPlaying=!videoPreview.paused&&!videoPreview.ended;
          videoPreview.pause();
          videoPreview.addEventListener("loadedmetadata",()=>{
            if(selectionId!==app.videoSelectionId)return;
            videoPreview.currentTime=Math.min(position,Math.max(0,videoPreview.duration-0.01));
            if(wasPlaying)videoPreview.play().catch(()=>{});
          },{once:true});
          videoPreview.src=state.url;videoPreview.load();
          setAudioStatus(state.has_audio?"兼容音轨已就绪 · 可调节音量或静音":"此视频没有音轨；视频仍可分析，警示提示音可正常使用。",!state.has_audio);
          return;
        }
        await new Promise(resolve=>setTimeout(resolve,700));
      }
      await releaseBrowserPreview(jobId);
    } catch(err) {
      if(selectionId===app.videoSelectionId)setAudioStatus("兼容音轨未就绪："+err.message+"。当前保留原文件预览。",true);
      await releaseBrowserPreview(jobId);
    }
  }
  window.addEventListener("pagehide",()=>{
    if(app.mediaJob)navigator.sendBeacon("/api/media/release",new Blob([JSON.stringify({job_id:app.mediaJob})],{type:"application/json"}));
  });
  function updateAudioControl() {
    const silent=videoPreview.muted||videoPreview.volume===0;
    videoAudioButton.innerHTML=silent
      ? '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4V5Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="m16 9 5 6m0-6-5 6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4V5Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M15.5 8.5a5 5 0 0 1 0 7m2-9a8 8 0 0 1 0 11" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
    videoAudioButton.setAttribute("aria-label",silent?"打开视频声音":"静音视频");
    videoAudioButton.title=silent?"打开声音":"静音视频";
    videoAudioButton.setAttribute("aria-pressed",String(silent));
    $("videoVolume").value=String(Math.round(videoPreview.volume*100));
    $("videoVolumeValue").textContent=silent?"静音":Math.round(videoPreview.volume*100)+"%";
  }
  videoPreview.volume=0.8;
  videoPreview.muted=false;
  videoAudioButton.addEventListener("click",()=>{
    const silent=videoPreview.muted||videoPreview.volume===0;
    if(silent){if(videoPreview.volume===0)videoPreview.volume=0.8;videoPreview.muted=false;}
    else videoPreview.muted=true;
    updateAudioControl();
  });
  videoPreview.addEventListener("volumechange",updateAudioControl);
  $("videoVolume").addEventListener("input",()=>{
    videoPreview.volume=Number($("videoVolume").value)/100;
    videoPreview.muted=videoPreview.volume===0;
    updateAudioControl();
  });
  videoPreview.addEventListener("error",()=>{
    if(app.selectedVideo)setAudioStatus("浏览器无法解码当前预览。请等待兼容音轨准备完成；若仍失败，请检查下方提示。",true);
  });
  updateAudioControl();
  fileInput.addEventListener("change",()=>selectVideo(fileInput.files?.[0]||null));
  function resetLiveScore() {
    app.inferenceResult=null;
    alertLatched=false;
    liveScoreOverlay.hidden=true;
    liveScoreOverlay.classList.remove("alert");
    $("liveScoreState").textContent="等待分析";
    $("liveScoreNumber").textContent="—";
    $("liveScoreWindow").textContent="当前窗口 —";
    $("liveScoreThreshold").textContent="阈值 —";
    $("liveScoreMode").textContent="—";
  }
  function updateLiveScore(allowBeep=false) {
    const result=app.inferenceResult,windows=result?.windows||[];
    if(!result||!windows.length){liveScoreOverlay.hidden=true;return;}
    const time=Number(videoPreview.currentTime)||0;
    let selected=null,held=false;
    for(const item of windows){
      const start=Number(item.start_seconds),end=Number(item.end_seconds);
      if(time+1e-6>=start&&time<end-1e-7&&(!selected||start>=Number(selected.start_seconds)))selected=item;
    }
    if(!selected&&time>=Number(windows[windows.length-1].end_seconds)){
      selected=windows[windows.length-1];held=true;
    }
    if(!selected){
      selected=windows.reduce((previous,item)=>Number(item.end_seconds)<=time+1e-6?item:previous,null);
      held=Boolean(selected);
    }
    const modeNames={sensitive:"Sensitive",moderate:"Moderate",certain:"Certain"};
    liveScoreOverlay.hidden=false;
    if(!selected){
      liveScoreOverlay.classList.remove("alert");
      $("liveScoreState").textContent="等待首个完整窗口";
      $("liveScoreNumber").textContent="—";
      $("liveScoreWindow").textContent=`模型至少需要 ${fmt(result.window_seconds,0)} 秒画面`;
      $("liveScoreThreshold").textContent=`阈值 ${pct(result.threshold)}`;
      $("liveScoreMode").textContent=modeNames[result.mode]||String(result.mode||"");
      return;
    }
    const probability=Number(selected.probability_fight),threshold=Number(result.threshold);
    const alert=Number.isFinite(probability)&&Number.isFinite(threshold)&&probability>=threshold;
    if(allowBeep&&!videoPreview.paused&&!videoPreview.seeking){
      if(alert&&!alertLatched&&$("alertBeepEnabled").checked)playAlertBeep();
      alertLatched=alert;
    }
    liveScoreOverlay.classList.toggle("alert",alert);
    $("liveScoreState").textContent=alert?"ALERT · 达到阈值":held?"当前时间超出窗口":"当前窗口 Fight 概率";
    $("liveScoreNumber").textContent=pct(probability);
    $("liveScoreWindow").textContent=`窗口 ${fmt(selected.start_seconds,2)}–${fmt(selected.end_seconds,2)} s${held?" · 保持末窗分数":""}`;
    $("liveScoreThreshold").textContent=`阈值 ${pct(result.threshold)}`;
    $("liveScoreMode").textContent=`${modeNames[result.mode]||String(result.mode||"")} · 验证集阈值`;
  }
  videoPreview.addEventListener("timeupdate",()=>updateLiveScore(true));
  videoPreview.addEventListener("play",()=>{unlockAlertAudio().then(()=>updateLiveScore(true)).catch(()=>{});});
  videoPreview.addEventListener("seeked",()=>{updateLiveScore();alertLatched=liveScoreOverlay.classList.contains("alert");});
  ["loadedmetadata","durationchange"].forEach(name=>videoPreview.addEventListener(name,()=>{fitFullscreenVideo();updateLiveScore();}));
  videoPreview.addEventListener("ended",()=>{updateLiveScore();alertLatched=false;});
  function setFullscreenPresentation() {
    const active=document.fullscreenElement===videoStage||videoStage.classList.contains("fullscreen-fallback");
    if(active)fitFullscreenVideo();
    else{videoCanvas.style.width="";videoCanvas.style.height="";}
    const label=active?"退出全屏":"全屏";
    $("videoFullscreenLabel").textContent=label;
    $("videoFullscreenButton").setAttribute("aria-label",active?"退出全屏播放":"全屏播放");
    $("videoFullscreenButton").title=active?"退出全屏播放":"全屏播放";
    document.body.classList.toggle("video-fullscreen-fallback",videoStage.classList.contains("fullscreen-fallback"));
  }
  function fitFullscreenVideo() {
    if(!(document.fullscreenElement===videoStage||videoStage.classList.contains("fullscreen-fallback")))return;
    const width=videoPreview.videoWidth,height=videoPreview.videoHeight;
    if(!width||!height)return;
    const scale=Math.min(window.innerWidth/width,window.innerHeight/height);
    videoCanvas.style.width=String(Math.max(1,Math.round(width*scale)))+"px";
    videoCanvas.style.height=String(Math.max(1,Math.round(height*scale)))+"px";
  }
  async function toggleVideoFullscreen() {
    if(document.fullscreenElement===videoStage){await document.exitFullscreen();return;}
    if(videoStage.classList.contains("fullscreen-fallback")){videoStage.classList.remove("fullscreen-fallback");setFullscreenPresentation();return;}
    if(videoStage.requestFullscreen){
      try{await videoStage.requestFullscreen({navigationUI:"hide"});return;}
      catch(_){try{await videoStage.requestFullscreen();return;}catch(__){}}
    }
    videoStage.classList.add("fullscreen-fallback");setFullscreenPresentation();
  }
  $("videoFullscreenButton").addEventListener("click",()=>toggleVideoFullscreen().catch(()=>{}));
  document.addEventListener("fullscreenchange",setFullscreenPresentation);
  window.addEventListener("resize",()=>{if(document.fullscreenElement===videoStage||videoStage.classList.contains("fullscreen-fallback"))fitFullscreenVideo();});
  document.addEventListener("keydown",event=>{if(event.key==="Escape"&&videoStage.classList.contains("fullscreen-fallback")){videoStage.classList.remove("fullscreen-fallback");setFullscreenPresentation();}});
  videoPreview.addEventListener("dblclick",event=>{event.preventDefault();toggleVideoFullscreen().catch(()=>{});});
  function selectVideo(file) {
    if(file){
      const ext=file.name.split(".").pop().toLowerCase();
      if(!["mp4","avi"].includes(ext)){fileInput.value="";showBanner("inferBanner","格式不支持。请选择 MP4 或 AVI 视频。","error");return;}
      if(file.size>300*1024*1024){fileInput.value="";showBanner("inferBanner","文件超过 300 MB 限制，请选择较小的视频。","error");return;}
    }
    app.videoSelectionId++;
    app.selectedVideo=file;
    const previousMediaJob=app.mediaJob;app.mediaJob=null;
    releaseBrowserPreview(previousMediaJob);
    resetLiveScore();
    if(app.uploadUrl)URL.revokeObjectURL(app.uploadUrl);app.uploadUrl=null;
    if(!file){
      setAudioStatus("选择视频后会自动准备兼容音轨。");
      $("fileName").textContent="尚未选择文件";videoPreview.pause();videoPreview.removeAttribute("src");videoPreview.load();videoStage.classList.remove("show");
      $("resultModelTag").textContent="等待视频分析";
      $("resultBody").innerHTML='<div class="result-placeholder"><div><strong>结果会显示在这里</strong><span>选择一个已完成的模型和视频后开始分析。</span></div></div>';
      return;
    }
    hideBanner("inferBanner");$("fileName").textContent=`${file.name} · ${(file.size/1024/1024).toFixed(1)} MB`;
    $("resultModelTag").textContent="等待视频分析";
    $("resultBody").innerHTML='<div class="result-placeholder"><div><strong>视频已载入</strong><span>开始分析后，播放时会在画面上显示当前窗口概率。</span></div></div>';
    updateAudioControl();
    app.uploadUrl=URL.createObjectURL(file);videoPreview.src=app.uploadUrl;videoPreview.load();videoStage.classList.add("show");
    prepareBrowserPreview(file,app.videoSelectionId);
  }
  ["dragenter","dragover"].forEach(name=>drop.addEventListener(name,event=>{event.preventDefault();drop.classList.add("drag");}));
  ["dragleave","drop"].forEach(name=>drop.addEventListener(name,event=>{event.preventDefault();drop.classList.remove("drag");}));
  drop.addEventListener("drop",event=>{const file=event.dataTransfer.files?.[0];if(file){try{const transfer=new DataTransfer();transfer.items.add(file);fileInput.files=transfer.files;}catch(_){}selectVideo(file);}});
  $("clearVideoButton").addEventListener("click",()=>{fileInput.value="";selectVideo(null);hideBanner("inferBanner");});
  function setInferProgress(progress,stage,detail="") {
    $("inferProgress").classList.add("show"); const p=Math.max(0,Math.min(100,Math.round((Number(progress)||0)*100)));
    $("inferStage").textContent=stage||"处理中";$("inferPercent").textContent=`${p}%`;$("inferProgressFill").style.width=`${p}%`;$("uploadProgressText").textContent=detail||"";
  }
  $("inferButton").addEventListener("click",()=>{
    const file=app.selectedVideo;const checkpoint=$("inferRun").value;
    if(!checkpoint){showBanner("inferBanner","请先选择一个已经完成训练、验证和测试的模型。","warn");return;}
    if(!file){showBanner("inferBanner","请先选择 MP4 或 AVI 视频。","warn");return;}
    app.inferenceSelectionId=app.videoSelectionId;resetLiveScore();
    hideBanner("inferBanner");$("inferButton").disabled=true;$("resultBody").innerHTML='<div class="result-placeholder"><div><strong>正在准备推理</strong><span>上传完成后会逐窗口显示进度。</span></div></div>';
    const form=new FormData();form.append("video",file,file.name);form.append("output_root",$("inferOutputRoot").value.trim()||$("outputRoot").value.trim());form.append("checkpoint",checkpoint);form.append("mode",$("inferMode").value);
    const xhr=new XMLHttpRequest();xhr.open("POST","/api/inference/start");xhr.upload.onprogress=event=>{if(event.lengthComputable)setInferProgress(event.loaded/event.total*.18,"正在上传视频",`${(event.loaded/1024/1024).toFixed(1)} / ${(event.total/1024/1024).toFixed(1)} MB`);};
    xhr.onerror=()=>{showBanner("inferBanner","无法连接本机推理服务，请确认 dashboard 仍在运行。","error");$("inferButton").disabled=false;};
    xhr.onload=()=>{let result={};try{result=JSON.parse(xhr.responseText);}catch(_){}if(xhr.status<200||xhr.status>=300){showBanner("inferBanner",result.error||`推理请求失败（HTTP ${xhr.status}）`,"error");$("inferButton").disabled=false;return;}app.inferenceJob=result.job_id;setInferProgress(.2,"视频已接收","开始读取模型与视频帧");pollInference();};
    setInferProgress(0,"准备上传","视频将发送至本机服务");xhr.send(form);
  });
  async function pollInference() {
    if(!app.inferenceJob)return;
    try {
      const state=await api("/api/inference/status");if(state.job_id!==app.inferenceJob){setTimeout(pollInference,800);return;}
      setInferProgress(state.progress||0,state.stage||"处理中",state.total_windows?`已处理 ${state.processed_windows||0} / ${state.total_windows} 个窗口`:"");
      if(state.status==="completed"){if(app.inferenceSelectionId===app.videoSelectionId)renderInferenceResult(state.result);$("inferButton").disabled=false;return;}
      if(state.status==="failed"){
        if(app.inferenceSelectionId===app.videoSelectionId){
          showBanner("inferBanner",state.error||"视频推理失败。","error");
          $("resultBody").innerHTML='<div class="result-placeholder"><div><strong>本次分析未完成</strong><span>请检查格式、视频解码和服务端日志。</span></div></div>';
        }
        $("inferButton").disabled=false;
        return;
      }
      setTimeout(pollInference,700);
    } catch(err) { showBanner("inferBanner",err.message,"error");$("inferButton").disabled=false; }
  }
  function renderInferenceResult(result) {
    if(!result)return;hideBanner("inferBanner");
    app.inferenceResult=result;
    const fight=result.prediction==="Fight",modeNames={sensitive:"Sensitive",moderate:"Moderate",certain:"Certain"};
    $("resultModelTag").textContent=modeNames[result.mode]||result.mode;
    const rows=(result.windows||[]).map((window,index)=>{
      const high=Number(window.probability_fight)>=Number(result.threshold);
      return `<tr><td>${String(index+1).padStart(2,"0")}</td><td>${fmt(window.start_seconds,2)}–${fmt(window.end_seconds,2)} s</td><td class="prob-cell"><span class="prob-track"><i class="prob-fill ${high?"high":""}" style="width:${Math.max(0,Math.min(100,Number(window.probability_fight)*100))}%"></i></span>${pct(window.probability_fight)}</td><td>${high?"达到阈值":"低于阈值"}</td></tr>`;
    }).join("");
    $("resultBody").innerHTML=`
      <div class="decision-banner ${fight?"fight":""}"><div><div class="decision-label">${escapeHtml(result.filename)} · ${modeNames[result.mode]||escapeHtml(result.mode)} 判定</div><div class="decision-value">${fight?"Fight · 暴力":"NonFight · 非暴力"}</div></div><div class="score-pill"><strong>${pct(result.probability_fight)}</strong><span>视频分数 / 阈值 ${pct(result.threshold)}</span></div></div>
      <div class="result-metrics"><div class="result-metric"><span>视频时长</span><strong>${fmt(result.duration_seconds,2)} 秒</strong></div><div class="result-metric"><span>分析窗口数</span><strong>${result.window_count} 个</strong></div><div class="result-metric"><span>分析耗时</span><strong>${fmt(result.elapsed_seconds,2)} 秒</strong></div><div class="result-metric"><span>计算设备</span><strong>${escapeHtml(result.device)}</strong></div></div>
      <div class="chart-panel" style="margin-bottom:15px"><div class="chart-title"><span>各窗口 Fight 概率</span><span>虚线为所选模式阈值</span></div><canvas id="windowChart" aria-label="各时间窗口的暴力概率"></canvas></div>
      <div class="section-label">窗口级结果 · ${result.window_count} 个片段</div>
      <div class="table-scroll"><table class="window-table"><thead><tr><th>#</th><th>时间位置</th><th>Fight 概率</th><th>与阈值比较</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="table-note">${result.window_seconds===4?"4 秒模型：长片按 2 秒步长滑动，必要时追加末尾对齐窗口；短片按真实时长均匀采样 16 帧。":"整条视频分数取所有完整 "+fmt(result.window_seconds,0)+" 秒窗口的最大 Fight 概率；窗口按 "+fmt(result.stride_seconds,2)+" 秒步长滑动。"}</div>`;
    drawWindowChart($("windowChart"),result.windows||[],Number(result.threshold));
    updateLiveScore();
    setInferProgress(1,"推理完成",`共分析 ${result.window_count} 个窗口`);
  }
  function drawWindowChart(canvas,windows,threshold) {
    if(!canvas)return;const rect=canvas.getBoundingClientRect(),scale=window.devicePixelRatio||1,width=Math.max(260,rect.width),height=112;
    canvas.width=Math.floor(width*scale);canvas.height=Math.floor(height*scale);const ctx=canvas.getContext("2d");ctx.scale(scale,scale);ctx.clearRect(0,0,width,height);
    const pad={l:31,r:9,t:9,b:19},plotW=width-pad.l-pad.r,plotH=height-pad.t-pad.b;
    ctx.font="9px system-ui";ctx.strokeStyle="#eaf0ed";ctx.fillStyle="#8c9a98";ctx.lineWidth=1;
    [0,0.5,1].forEach((value,index)=>{const y=pad.t+plotH*(1-value);ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(width-pad.r,y);ctx.stroke();ctx.fillText(`${Math.round(value*100)}%`,2,y+3);});
    if(!windows.length){ctx.fillText("没有完整窗口",pad.l+10,height/2);return;}
    const thresholdY=pad.t+plotH*(1-threshold);ctx.save();ctx.setLineDash([4,4]);ctx.strokeStyle="#c44d4e";ctx.beginPath();ctx.moveTo(pad.l,thresholdY);ctx.lineTo(width-pad.r,thresholdY);ctx.stroke();ctx.restore();
    ctx.strokeStyle="#087f78";ctx.lineWidth=2;ctx.lineJoin="round";ctx.lineCap="round";ctx.beginPath();
    windows.forEach((windowItem,index)=>{const x=pad.l+(windows.length===1?plotW/2:plotW*index/(windows.length-1));const y=pad.t+plotH*(1-Number(windowItem.probability_fight));if(index===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();
    windows.forEach((windowItem,index)=>{const x=pad.l+(windows.length===1?plotW/2:plotW*index/(windows.length-1));const y=pad.t+plotH*(1-Number(windowItem.probability_fight));ctx.beginPath();ctx.fillStyle="#fff";ctx.arc(x,y,3,0,Math.PI*2);ctx.fill();ctx.strokeStyle="#087f78";ctx.lineWidth=1.6;ctx.stroke();});
    ctx.fillStyle="#8c9a98";ctx.fillText("1",pad.l,height-3);ctx.fillText(String(windows.length),Math.max(pad.l,width-pad.r-7),height-3);
  }
  $("inferRun").addEventListener("change",()=>{const run=app.runs.find(item=>item.best_model===$("inferRun").value);if(run){$("resultModelTag").textContent=run.name;updateInferenceProtocol(run);}else updateInferenceProtocol(null);});

  loadHealth().then(()=>{loadRuns(true);loadReverseDatasetSummary();});
  pollStatus();
  app.pollTimer=setInterval(()=>{pollStatus();pollReverseStatus();pollFourStatus();},1400);
