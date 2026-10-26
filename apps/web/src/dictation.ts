// Quick note 🎙 (task #143): one voice path, as easy as dictation in the Claude app. Tap, talk, watch the transcript
// appear live, tap again; the text lands in the note to edit before it is sent. Two ways to hear, one behaviour:
// Whisper on this PC's own hardware when Atlas runs on the PC ('pc', services/atlas/localvoice.py; the Pi never
// transcribes on its CPU), Whisper through the Engine (a Whisper server or a hosted API, set up under Systems), or the
// browser's own speech recognition (instant, no setup). With Whisper the recording so far is sent every few seconds
// for a live preview, the whole recording at Stop.
import {useEffect,useRef,useState} from 'react';

export type Hearing='pc'|'whisper'|'browser';
export const HEARING_TEXT:Record<Hearing,string>={pc:'Whisper on this PC',whisper:'Whisper server (Engine)',browser:'Browser dictation'};
const PREVIEW_EVERY=3000,PREVIEW_UNTIL=120_000;  // live Whisper previews stop after two minutes; the final transcript still comes at Stop
const TYPES=['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/ogg'];

function speechApi(){return (window as any).SpeechRecognition||(window as any).webkitSpeechRecognition}
function blobBase64(b:Blob){return new Promise<string>((ok,bad)=>{const fr=new FileReader();fr.onload=()=>ok(String(fr.result).split(',')[1]||'');fr.onerror=()=>bad(Error('could not read the recording'));fr.readAsDataURL(b)})}
async function whisper(blob:Blob,partial:boolean,pc:boolean){
 const url=pc?'/api/voice/local/transcribe':'/api/engine/engine.brain.voice_input';
 const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:await blobBase64(blob),type:blob.type,partial})});const b=await r.json();
 if(!r.ok)throw Error(b.error||(pc?'This PC could not write it out':'The Engine refused'));return String((pc?b.text:b.message)||'').trim()}
export function savedHearing():Hearing|null{try{return localStorage.getItem('atlas-hearing') as Hearing|null}catch{return null}}
export function saveHearing(h:Hearing){try{localStorage.setItem('atlas-hearing',h)}catch{}window.dispatchEvent(new Event('atlas-hearing'))}
export function joinText(before:string,heard:string){heard=heard.trim();if(!heard)return before;return before.trim()?before.trimEnd()+' '+heard:heard}

export function useDictation({canWhisper,canPC=false,pcPreviews=false,append,say}:{canWhisper:boolean;canPC?:boolean;pcPreviews?:boolean;append:(text:string)=>void;say:(message:string)=>void}){
 const hasBrowser=!!speechApi(),hasRecorder=typeof MediaRecorder!=='undefined'&&!!navigator.mediaDevices?.getUserMedia;
 const options:Hearing[]=[...(canPC&&hasRecorder?['pc' as Hearing]:[]),...(canWhisper&&hasRecorder?['whisper' as Hearing]:[]),...(hasBrowser?['browser' as Hearing]:[])];
 const [pick,setPick]=useState<Hearing|null>(savedHearing);
 const hearing:Hearing=pick&&options.includes(pick)?pick:options[0]||'browser';  // this PC first, then a Whisper server, then the browser
 const available=options.includes(hearing);
 const [listening,setListening]=useState(false),[live,setLive]=useState(''),[working,setWorking]=useState(false);
 const want=useRef(false),recog=useRef<any>(null),rec=useRef<MediaRecorder|null>(null),timer=useRef<number>(0);
 useEffect(()=>{const f=()=>setPick(savedHearing());window.addEventListener('atlas-hearing',f);return()=>window.removeEventListener('atlas-hearing',f)},[]);  // chosen under Systems › Voice
 useEffect(()=>()=>{want.current=false;recog.current?.abort?.();rec.current?.stream.getTracks().forEach(t=>t.stop());clearInterval(timer.current)},[]);
 function choose(h:Hearing){saveHearing(h);setPick(h)}

 function startBrowser(){const SR=speechApi();const r=new SR();r.lang=navigator.language||'en-GB';r.continuous=true;r.interimResults=true;
  r.onresult=(e:any)=>{let done='',now='';for(let i=e.resultIndex;i<e.results.length;i++){const t=e.results[i][0].transcript;if(e.results[i].isFinal)done+=t;else now+=t}if(done)append(done);setLive(now)};
  r.onerror=(e:any)=>{if(e.error==='no-speech'||e.error==='aborted')return;want.current=false;say(e.error==='not-allowed'?'Atlas may not use the microphone: allow it in the browser and tap 🎙 again.':'Dictation stopped: '+(e.error||'unknown'))};
  r.onend=()=>{if(want.current){try{r.start();return}catch{}}setLive('');setListening(false)};  // the browser stops after a silence; keep listening until Stop
  recog.current=r;r.start();setListening(true);say('Listening… the text appears as you talk. Tap 🎙 to stop, then edit and send.')}

 async function startWhisper(){let stream:MediaStream;
  try{stream=await navigator.mediaDevices.getUserMedia({audio:true})}catch{say('Atlas may not use the microphone: allow it in the browser and tap 🎙 again.');return}
  const type=TYPES.find(t=>MediaRecorder.isTypeSupported?.(t));const r=type?new MediaRecorder(stream,{mimeType:type}):new MediaRecorder(stream);
  const chunks:Blob[]=[],started=Date.now();let busy=false,sent=0;
  r.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
  const pc=hearing==='pc',previews=!pc||pcPreviews;  // on a PC without a usable GPU, no previews: only the final transcript
  timer.current=window.setInterval(async()=>{if(!previews||busy||chunks.length===sent||Date.now()-started>PREVIEW_UNTIL)return;busy=true;sent=chunks.length;
   try{const t=await whisper(new Blob(chunks,{type:r.mimeType}),true,pc);if(want.current&&t)setLive(t)}catch{}finally{busy=false}},PREVIEW_EVERY);
  r.onstop=async()=>{clearInterval(timer.current);stream.getTracks().forEach(t=>t.stop());setListening(false);
   if(!chunks.length){setLive('');say('Nothing was recorded.');return}
   setWorking(true);say('Writing out the recording…');
   try{const t=await whisper(new Blob(chunks,{type:r.mimeType}),false,pc);setLive('');if(t){append(t);say('Done: edit the text if needed, then send.')}else say('Nothing was heard.')}
   catch(e){setLive('');say((e as Error).message||'Speech-to-text failed.')}finally{setWorking(false)}};
  rec.current=r;r.start(1000);setListening(true);say(previews?'Recording… a live transcript follows every few seconds. Tap 🎙 to stop, then edit and send.':'Recording on this PC… tap 🎙 to stop; the text follows, then edit and send.')}

 function toggle(){
  if(listening){want.current=false;recog.current?.stop();if(rec.current?.state==='recording')rec.current.stop();return}
  if(working)return;
  if(!available){say(hasRecorder?'Dictation is not available here: set up voice under Systems › Voice, or use Chrome or Edge.':'This browser cannot record audio.');return}
  want.current=true;setLive('');if(hearing==='browser')startBrowser();else startWhisper()}
 const next=()=>{const i=options.indexOf(hearing);choose(options[(i+1)%options.length])};
 return {listening,working,live,hearing,options,choose,next,toggle,canChoose:options.length>1};
}
