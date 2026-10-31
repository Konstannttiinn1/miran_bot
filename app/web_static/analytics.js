"use strict";

const COPY = {
  ru: {
    analytics:"Аналитика", overview:"Сводка", users:"Пользователи", campaigns:"Кампании", admin:"Администратор", dealer:"Дилер", allProject:"Весь проект", refresh:"Обновить", close:"Закрыть",
    overviewSub:"Главное о продажах и клиентах", usersSub:"Оплаты и текущие сроки подписок", campaignsSub:"От запуска бота до покупки", today:"Сегодня", yesterday:"Вчера", "7d":"Последние 7 дней", "30d":"Последние 30 дней", month:"Этот месяц", prev_month:"Прошлый месяц", all:"За всё время",
    subscriptions:"Продажи", buyers:"Покупатели", addons:"Докупки трафика", amounts:"Суммы продаж", confirmed:"Подтверждённые операции", currencyNote:"USD — дилерские списания. Валюты показаны отдельно; пополнения баланса не входят в продажи.", retail:"Розница по сохранённым заказам", estimated:"У части старых операций дата оплаты восстановлена приблизительно", paidUsers:"Оплачивали", unpaidUsers:"Не оплачивали", recent:"Последние продажи", moscow:"Время МСК", noSales:"Продаж за этот период пока нет", noSalesSub:"Здесь появятся подтверждённые покупки, продления и докупки трафика.",
    paid:"Оплачивали", unpaid:"Не оплачивали", any:"Все", paidBadge:"Оплачен", unpaidBadge:"Без оплаты", otherPayment:"Оплата вне кампании", search:"Имя или Telegram ID", find:"Найти", telegram:"Пользователи бота", managed:"Подписки дилера", managedBadge:"Подписка дилера", expires:"До", expired:"Истекла", noSubscription:"Без подписки", noProduct:"Тариф не сохранён", days:"дн.", devices:"устр.", traffic:"ГБ", monthly:"ГБ/мес", records:"Записей", back:"Назад", next:"Далее", noUsers:"Пользователи не найдены", noUsersSub:"Попробуй другой фильтр или поисковый запрос.",
    newCampaign:"Новая кампания", name:"Название", namePlaceholder:"Например, канал · ноябрь", campaignHint:"Одна ссылка для одного размещения.", create:"Создать ссылку", creating:"Создаём…", campaignLink:"Рекламная ссылка", copy:"Скопировать ссылку", copied:"Ссылка скопирована", selectLink:"Нажми на поле, чтобы выделить ссылку", visitors:"Зашли", bought:"Купили", conversion:"Конверсия", people:"Пользователи", noCampaigns:"Здесь будут рекламные кампании", noCampaignsSub:"Создай ссылку для канала или размещения. Увидишь, кто запустил бота и затем купил.", attribution:"Покупки относятся к первой рекламной ссылке пользователя. Считаются люди, запустившие бота, без повторных запусков.", lifetime:"Статистика за всё время", clearCampaign:"Убрать фильтр кампании",
    openTelegram:"Открой из Telegram", openTelegramSub:"Аналитика доступна администратору и дилерам через кнопку в меню бота.", expiredSession:"Открой мини-приложение заново", expiredSessionSub:"Сессия Telegram завершилась. Вернись в бот и снова нажми «Аналитика».", denied:"Нет доступа", deniedSub:"Этот раздел доступен администратору и активным дилерам.", error:"Не удалось загрузить данные", errorSub:"Проверь соединение и попробуй ещё раз.", retry:"Повторить", invalidName:"Укажи название от 1 до 80 символов", campaignError:"Не удалось создать кампанию. Попробуй ещё раз.", creatingFor:"Для выбранного дилера"
  },
  en: {
    analytics:"Analytics", overview:"Overview", users:"Customers", campaigns:"Campaigns", admin:"Administrator", dealer:"Dealer", allProject:"Whole project", refresh:"Refresh", close:"Close",
    overviewSub:"Sales and customers at a glance", usersSub:"Payments and current subscription terms", campaignsSub:"From starting the bot to buying", today:"Today", yesterday:"Yesterday", "7d":"Last 7 days", "30d":"Last 30 days", month:"This month", prev_month:"Previous month", all:"All time",
    subscriptions:"Sales", buyers:"Buyers", addons:"Traffic add-ons", amounts:"Sales amounts", confirmed:"Confirmed transactions", currencyNote:"USD shows dealer debits. Currencies stay separate; balance top-ups are excluded.", retail:"Retail from saved orders", estimated:"Some historical payment dates are approximate", paidUsers:"Have paid", unpaidUsers:"Never paid", recent:"Recent sales", moscow:"Moscow time", noSales:"No sales in this period yet", noSalesSub:"Confirmed purchases, renewals and traffic add-ons will appear here.",
    paid:"Have paid", unpaid:"Never paid", any:"All", paidBadge:"Paid", unpaidBadge:"No payment", otherPayment:"Paid outside campaign", search:"Name or Telegram ID", find:"Search", telegram:"Bot customers", managed:"Dealer subscriptions", managedBadge:"Dealer subscription", expires:"Until", expired:"Expired", noSubscription:"No subscription", noProduct:"Plan unavailable", days:"days", devices:"devices", traffic:"GB", monthly:"GB/month", records:"Records", back:"Previous", next:"Next", noUsers:"No customers found", noUsersSub:"Try another filter or search term.",
    newCampaign:"New campaign", name:"Name", namePlaceholder:"For example, channel · November", campaignHint:"One link for each placement.", create:"Create link", creating:"Creating…", campaignLink:"Campaign link", copy:"Copy link", copied:"Link copied", selectLink:"Tap the field to select the link", visitors:"Started", bought:"Bought", conversion:"Conversion", people:"Customers", noCampaigns:"Your campaigns will appear here", noCampaignsSub:"Create a link for a channel or placement. See who started the bot and later bought.", attribution:"Purchases belong to the user's first campaign. Unique people who start the bot are counted, without repeated starts.", lifetime:"All-time statistics", clearCampaign:"Clear campaign filter",
    openTelegram:"Open from Telegram", openTelegramSub:"Administrators and dealers can open analytics from their bot menu.", expiredSession:"Reopen the Mini App", expiredSessionSub:"Your Telegram session expired. Return to the bot and tap Analytics again.", denied:"Access denied", deniedSub:"Analytics is available to administrators and active dealers.", error:"Could not load data", errorSub:"Check your connection and try again.", retry:"Try again", invalidName:"Enter a name from 1 to 80 characters", campaignError:"Could not create the campaign. Please try again.", creatingFor:"For the selected dealer"
  },
  fa: {
    analytics:"آمار", overview:"نمای کلی", users:"کاربران", campaigns:"کمپین‌ها", admin:"مدیر", dealer:"نماینده", allProject:"کل پروژه", refresh:"تازه‌سازی", close:"بستن",
    overviewSub:"فروش و کاربران در یک نگاه", usersSub:"پرداخت‌ها و مدت فعلی اشتراک‌ها", campaignsSub:"از ورود به ربات تا خرید", today:"امروز", yesterday:"دیروز", "7d":"۷ روز گذشته", "30d":"۳۰ روز گذشته", month:"این ماه میلادی", prev_month:"ماه میلادی قبل", all:"همه زمان‌ها",
    subscriptions:"فروش", buyers:"خریداران", addons:"ترافیک اضافه", amounts:"مبالغ فروش", confirmed:"تراکنش‌های تأییدشده", currencyNote:"USD هزینه کسرشده از موجودی نماینده است. ارزها جدا هستند و شارژ موجودی، فروش محسوب نمی‌شود.", retail:"مبلغ خرده‌فروشی سفارش‌های ثبت‌شده", estimated:"تاریخ پرداخت بعضی سفارش‌های قدیمی تقریبی است", paidUsers:"پرداخت کرده‌اند", unpaidUsers:"پرداخت نکرده‌اند", recent:"فروش‌های اخیر", moscow:"زمان مسکو", noSales:"هنوز فروشی در این بازه نیست", noSalesSub:"خرید، تمدید و ترافیک اضافه پس از تأیید اینجا نمایش داده می‌شوند.",
    paid:"پرداخت کرده", unpaid:"پرداخت نکرده", any:"همه", paidBadge:"پرداخت‌شده", unpaidBadge:"بدون پرداخت", otherPayment:"پرداخت خارج از کمپین", search:"نام یا شناسه تلگرام", find:"جستجو", telegram:"کاربران ربات", managed:"اشتراک‌های نماینده", managedBadge:"اشتراک نماینده", expires:"تا", expired:"منقضی", noSubscription:"بدون اشتراک", noProduct:"طرح ثبت نشده", days:"روز", devices:"دستگاه", traffic:"گیگ", monthly:"گیگ در ماه", records:"تعداد", back:"قبلی", next:"بعدی", noUsers:"کاربری پیدا نشد", noUsersSub:"فیلتر یا عبارت جستجو را تغییر بده.",
    newCampaign:"کمپین جدید", name:"نام", namePlaceholder:"مثلاً کانال · آبان", campaignHint:"برای هر تبلیغ یک لینک جدا.", create:"ساخت لینک", creating:"در حال ساخت…", campaignLink:"لینک تبلیغاتی", copy:"کپی لینک", copied:"لینک کپی شد", selectLink:"برای انتخاب لینک روی کادر بزن", visitors:"وارد شدند", bought:"خرید کردند", conversion:"نرخ خرید", people:"کاربران", noCampaigns:"کمپین‌ها اینجا نمایش داده می‌شوند", noCampaignsSub:"برای کانال یا تبلیغ لینک بساز و ببین چه کسی وارد ربات شده و بعد خرید کرده است.", attribution:"خرید به اولین لینک تبلیغاتی کاربر نسبت داده می‌شود. هر کاربر یک بار شمرده می‌شود، نه هر بار ورود.", lifetime:"آمار همه زمان‌ها", clearCampaign:"حذف فیلتر کمپین",
    openTelegram:"از تلگرام باز کن", openTelegramSub:"مدیر و نماینده‌ها می‌توانند از دکمه آمار در منوی ربات وارد شوند.", expiredSession:"مینی‌اپ را دوباره باز کن", expiredSessionSub:"نشست تلگرام تمام شده است. به ربات برگرد و دوباره دکمه آمار را بزن.", denied:"دسترسی نداری", deniedSub:"این بخش مخصوص مدیر و نماینده‌های فعال است.", error:"دریافت اطلاعات ممکن نشد", errorSub:"اتصال اینترنت را بررسی کن و دوباره تلاش کن.", retry:"تلاش دوباره", invalidName:"نامی بین ۱ تا ۸۰ نویسه وارد کن", campaignError:"ساخت کمپین ممکن نشد. دوباره تلاش کن.", creatingFor:"برای نماینده انتخاب‌شده"
  }
};

const tg = window.Telegram?.WebApp;
const state = {lang:"ru",tab:"overview",period:"30d",dealer:"",paid:"paid",q:"",page:0,kind:"telegram",campaign:null,me:null,overview:null};
const main = document.getElementById("main");
const dialog = document.getElementById("campaign-dialog");
const appBase = new URL("./", window.location.href);
let controller, generation = 0, toastTimer;
const t = key => (COPY[state.lang] || COPY.ru)[key] || COPY.ru[key] || key;
const locale = () => state.lang === "fa" ? "fa-IR" : state.lang === "ru" ? "ru-RU" : "en-US";
const count = value => new Intl.NumberFormat(locale(),{maximumFractionDigits:1}).format(value || 0);
const amount = (value, currency) => new Intl.NumberFormat(locale(),{maximumFractionDigits:currency === "XTR" ? 0 : 8}).format(Number(value || 0));
const date = value => value ? new Intl.DateTimeFormat(locale(),{day:"numeric",month:"short",year:"numeric",timeZone:"Europe/Moscow"}).format(new Date(value)) : "";
function node(tag, css="", text="") {const n=document.createElement(tag);if(css)n.className=css;if(text!==undefined)n.textContent=text;return n;}
function button(text,css,action){const b=node("button",css,text);b.type="button";b.addEventListener("click",action);return b;}
function toast(text){const n=document.getElementById("toast");n.textContent=text;n.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>n.hidden=true,2600);}
function productText(p){if(!p)return t("noProduct");if(!p.days)return p.code === "traffic50" ? `+${count(p.traffic_gb)} ${t("traffic")}` : p.code;return `${count(p.days)} ${t("days")} · ${count(p.devices)} ${t("devices")}`;}
function applyTheme(){document.body.dataset.theme=tg?.colorScheme || (matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");}

async function api(path,{method="GET",body,signal}={}){
  const response=await fetch(new URL(`api/${path}`,appBase),{method,signal,cache:"no-store",headers:{"Authorization":`tma ${tg?.initData || ""}`,"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
  if(!response.ok){const error=new Error("API request failed");error.status=response.status;throw error;}
  return response.json();
}
function query(extra={}){const params=new URLSearchParams();if(state.dealer)params.set("dealer",state.dealer);for(const[k,v]of Object.entries(extra))if(v!==null&&v!==undefined&&v!=="")params.set(k,v);return params.toString();}
function lock(title,description,retry=false){main.replaceChildren();const section=node("section","welcome");section.append(node("div","lock","↗"),node("h1","",t(title)),node("p","",t(description)));if(retry)section.append(button(t("retry"),"primary-button",()=>state.me?load():boot()));main.append(section);}
function translateChrome(){
  document.documentElement.lang=state.lang;document.documentElement.dir=state.lang==="fa"?"rtl":"ltr";
  document.getElementById("brand-sub").textContent=t("analytics");document.getElementById("refresh").setAttribute("aria-label",t("refresh"));
  document.getElementById("role").textContent=state.me?t(state.me.role):"";document.getElementById("role").hidden=!state.me;
  document.getElementById("nav").setAttribute("aria-label",t("analytics"));
  document.querySelectorAll("[data-tab]").forEach(b=>{b.querySelector("span").textContent=t(b.dataset.tab);});
  for(const[id,key]of Object.entries({"dialog-title":"newCampaign","name-label":"name","campaign-hint":"campaignHint","create-campaign":"create","link-label":"campaignLink","copy-created":"copy"}))document.getElementById(id).textContent=t(key);
  document.getElementById("campaign-name").placeholder=t("namePlaceholder");document.getElementById("close-dialog").setAttribute("aria-label",t("close"));
}
function navigate(tab){state.tab=tab;state.page=0;load();window.scrollTo({top:0,behavior:"instant"});}
function scaffold(){
  main.replaceChildren();
  const heading=node("div","page-heading"),words=node("div");words.append(node("h1","",t(state.tab)),node("p","subtitle",t(`${state.tab}Sub`)));heading.append(words);
  if(state.tab==="overview"){
    const select=node("select","period-select");select.setAttribute("aria-label",t("overview"));
    for(const p of["today","yesterday","7d","30d","month","prev_month","all"]){const o=node("option","",t(p));o.value=p;o.selected=state.period===p;select.append(o);}select.addEventListener("change",()=>{state.period=select.value;load();});heading.append(select);
  }
  if(state.tab==="campaigns")heading.append(button(`+ ${t("newCampaign")}`,"primary-button",openCampaign));
  main.append(heading);
  if(state.me?.role==="admin" && state.dealers?.length){
    const scope=node("div","scope-bar"),select=node("select");select.setAttribute("aria-label",t("dealer"));
    const all=node("option","",t("allProject"));all.value="";select.append(all);for(const d of state.dealers){const o=node("option","",d.name);o.value=String(d.telegram_id);select.append(o);}select.value=state.dealer;
    select.addEventListener("change",()=>{state.dealer=select.value;state.campaign=null;state.kind="telegram";state.page=0;load();});scope.append(select);main.append(scope);
  }
  document.querySelectorAll("[data-tab]").forEach(b=>{if(b.dataset.tab===state.tab)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");});
  const content=node("div");content.id="content";main.append(content);return content;
}
function loading(content){const row=node("div","metrics");for(let i=0;i<3;i++)row.append(node("div","skeleton"));content.append(row,node("div","skeleton big"));}
function empty(content,title,description,action){const box=node("div","card empty");box.append(node("h2","",t(title)),node("p","",t(description)));if(action)box.append(button(t("newCampaign"),"primary-button",action));content.append(box);}
function showOverview(content,data){
  const metrics=node("div","metrics");for(const[key,label]of[["subscriptions","subscriptions"],["buyers","buyers"],["traffic_addons","addons"]]){const box=node("div","metric");box.append(node("div","metric-label",t(label)),node("div","metric-value",count(data.sales[key])));metrics.append(box);}content.append(metrics);
  const money=node("section","card");const head=node("div","section-head");head.append(node("h2","",t("amounts")),node("span","section-label",t("confirmed")));money.append(head);
  const values=node("div","money-list");if(!data.totals.length)values.append(node("div","money-value",count(0)));
  for(const item of data.totals){const box=node("div");box.append(node("div","money-value",amount(item.amount,item.currency)),node("div","money-code",item.currency==="XTR"?"Telegram Stars":item.currency));values.append(box);}money.append(values);
  const notes=node("div","money-note");notes.append(node("p","muted",t("currencyNote")));if(data.retail_toman!==null)notes.append(node("p","muted",`${t("retail")}: ${amount(data.retail_toman,"TMN")} ${state.lang==="fa"?"تومان":"TMN"}`));if(data.estimated_count)notes.append(node("p","muted",`${t("estimated")} (${count(data.estimated_count)}).`));money.append(notes);content.append(money);
  const people=node("div","user-counts");for(const[filter,label]of[["paid","paidUsers"],["unpaid","unpaidUsers"]]){const b=button("","count-link",()=>{state.paid=filter;state.kind="telegram";state.campaign=null;navigate("users");});b.append(node("span","",t(label)),node("strong","",count(data.users[filter])));people.append(b);}content.append(people);
  if(!data.recent.length){empty(content,"noSales","noSalesSub");return;}
  const recent=node("section","card");const recentHead=node("div","section-head");recentHead.append(node("h2","",t("recent")),node("span","section-label",t("moscow")));recent.append(recentHead);
  for(const sale of data.recent){const row=node("div","recent-row");row.append(node("div","avatar",sale.name.slice(0,1).toUpperCase()));const desc=node("div","row-main");desc.append(node("div","row-name",sale.name),node("div","row-description",productText(sale.product)));const right=node("div","row-right");right.append(node("div","row-amount",`${amount(sale.amount,sale.currency)} ${sale.currency}`),node("div","row-description",date(sale.date)));row.append(desc,right);recent.append(row);}content.append(recent);
}
function userFilters(content){
  if(state.campaign){const chip=node("div","context-chip");chip.append(node("span","",state.campaign.name),button("×","",()=>{state.campaign=null;state.page=0;load();}));chip.lastChild.setAttribute("aria-label",t("clearCampaign"));content.append(chip);}
  const filters=node("div","filter-bar"),seg=node("div","segmented");for(const[key,label]of[["paid","paid"],["unpaid","unpaid"],["all","any"]]){const b=button(t(label),"",()=>{state.paid=key;state.page=0;load();});b.setAttribute("aria-pressed",String(state.paid===key));seg.append(b);}filters.append(seg);
  if(!state.campaign){const select=node("select","kind-select");select.setAttribute("aria-label",t("users"));for(const k of["telegram","managed"]){const o=node("option","",t(k));o.value=k;select.append(o);}select.value=state.kind;select.addEventListener("change",()=>{state.kind=select.value;state.page=0;load();});filters.append(select);}
  const search=node("form","search"),label=node("label","sr-only",t("search")),input=node("input");input.id="user-search";input.type="search";input.maxLength=80;input.placeholder=t("search");input.value=state.q;label.htmlFor=input.id;const submit=node("button","secondary-button","↵");submit.type="submit";submit.setAttribute("aria-label",t("find"));search.append(label,input,submit);search.addEventListener("submit",e=>{e.preventDefault();state.q=input.value.trim();state.page=0;load();});filters.append(search);content.append(filters);
}
function showUsers(content,data){
  userFilters(content);content.append(node("p","muted",`${t("records")}: ${count(data.total)}`));
  if(!data.items.length){empty(content,"noUsers","noUsersSub");return;}
  const list=node("section","card");list.style.marginTop="12px";
  for(const user of data.items){const row=node("div","user-row"),left=node("div","row-main"),title=node("div","row-title");title.append(node("span","row-name",user.name),node("span",`badge ${user.paid?"paid":""}`,t(user.paid?"paidBadge":user.paid_before?"otherPayment":"unpaidBadge")));left.append(title,node("div","row-description",`${productText(user.product)}${user.kind==="managed"?` · ${t("managedBadge")}`:""}`));if(user.campaign_name)left.append(node("div","row-description",user.campaign_name));
    const right=node("div","row-right");right.append(node("div",`expiry ${user.expires_at&&!user.active?"expired":""}`,user.expires_at?`${t(user.active?"expires":"expired")} ${date(user.expires_at)}`:t("noSubscription")));if(user.active)right.append(node("div","row-description",`${count(user.remaining_days)} ${t("days")}`));row.append(left,right);list.append(row);}content.append(list);
  const pager=node("div","pager"),prev=button(t("back"),"secondary-button",()=>{state.page=data.page-1;load();}),next=button(t("next"),"secondary-button",()=>{state.page=data.page+1;load();});prev.disabled=data.page===0;next.disabled=!data.has_more;pager.append(prev,node("span","",`${count(data.page+1)} / ${count(Math.max(1,Math.ceil(data.total/20)))}`),next);content.append(pager);
}
function showCampaigns(content,data){
  content.append(node("p","muted",t("attribution")));
  if(!data.items.length){empty(content,"noCampaigns","noCampaignsSub",openCampaign);return;}
  const grid=node("div","campaign-grid");grid.style.marginTop="18px";
  for(const campaign of data.items){const card=node("section","card campaign-card"),head=node("div","campaign-top"),title=node("div");title.append(node("div","campaign-name",campaign.name),node("div","row-description",t("lifetime")));head.append(title);card.append(head);
    const stats=node("div","campaign-metrics");for(const[key,label]of[["visitors","visitors"],["buyers","bought"],["conversion","conversion"]]){const item=node("div");item.append(node("strong","",count(campaign[key])+(key==="conversion"?"%":"")),node("span","",t(label)));stats.append(item);}card.append(stats);
    const actions=node("div","campaign-actions");actions.append(button(t("copy"),"secondary-button",()=>copyLink(campaign.link)),button(t("people"),"secondary-button",()=>{state.campaign=campaign;state.kind="telegram";state.paid="all";state.q="";navigate("users");}));card.append(actions);grid.append(card);}content.append(grid);
}
async function load(){
  if(!state.me)return;controller?.abort();controller=new AbortController();const current=++generation,content=scaffold();loading(content);
  try{let data;if(state.tab==="overview")data=await api(`overview?${query({period:state.period})}`,{signal:controller.signal});else if(state.tab==="users")data=await api(`users?${query({paid:state.paid,kind:state.kind,q:state.q,page:state.page,campaign:state.campaign?.id})}`,{signal:controller.signal});else data=await api(`campaigns?${query()}`,{signal:controller.signal});if(current!==generation)return;content.replaceChildren();if(state.tab==="overview"){state.overview=data;showOverview(content,data);}else if(state.tab==="users")showUsers(content,data);else showCampaigns(content,data);
  }catch(error){if(error.name==="AbortError"||current!==generation)return;if(error.status===401){lock("expiredSession","expiredSessionSub");document.getElementById("nav").hidden=true;}else if(error.status===403){lock("denied","deniedSub");}else{content.replaceChildren();const box=node("div","card empty");box.append(node("h2","",t("error")),node("p","",t("errorSub")),button(t("retry"),"primary-button",()=>load()));content.append(box);}}
}
function campaignDialogMode(create){for(const id of["name-label","campaign-name","campaign-hint"])document.getElementById(id).hidden=!create;document.getElementById("campaign-name").disabled=!create;document.getElementById("dialog-title").textContent=t(create?"newCampaign":"campaignLink");document.getElementById("create-campaign").hidden=!create;document.getElementById("created-link").hidden=create;}
function openCampaign(){document.getElementById("campaign-form").reset();campaignDialogMode(true);document.getElementById("form-error").hidden=true;document.getElementById("create-campaign").disabled=false;dialog.showModal();}
async function copyLink(link){try{await navigator.clipboard.writeText(link);toast(t("copied"));}catch{campaignDialogMode(false);document.getElementById("form-error").hidden=true;document.getElementById("link-value").value=link;if(!dialog.open)dialog.showModal();document.getElementById("link-value").select();toast(t("selectLink"));}}
document.getElementById("campaign-form").addEventListener("submit",async event=>{
  event.preventDefault();const name=document.getElementById("campaign-name").value.trim(),submit=document.getElementById("create-campaign"),error=document.getElementById("form-error");error.hidden=true;
  if(submit.disabled||submit.hidden)return;
  if(!name||name.length>80){error.textContent=t("invalidName");error.hidden=false;return;}submit.disabled=true;submit.textContent=t("creating");
  try{const campaign=await api(`campaigns?${query()}`,{method:"POST",body:{name}});document.getElementById("created-link").hidden=false;document.getElementById("link-value").value=campaign.link;document.getElementById("campaign-name").disabled=true;submit.hidden=true;if(state.tab==="campaigns")await load();}
  catch(err){error.textContent=err.status===401?t("expiredSessionSub"):t("campaignError");error.hidden=false;}
  finally{submit.disabled=false;submit.textContent=t("create");}
});
document.getElementById("close-dialog").addEventListener("click",()=>dialog.close());
document.getElementById("copy-created").addEventListener("click",()=>copyLink(document.getElementById("link-value").value));
document.getElementById("link-value").addEventListener("click",event=>event.target.select());
document.getElementById("refresh").addEventListener("click",()=>load());
document.querySelectorAll("[data-tab]").forEach(b=>b.addEventListener("click",()=>navigate(b.dataset.tab)));
async function boot(){
  applyTheme();tg?.ready();tg?.expand();tg?.onEvent("themeChanged",applyTheme);
  const initial=tg?.initDataUnsafe?.user?.language_code?.split("-")[0];if(COPY[initial])state.lang=initial;translateChrome();
  if(!tg?.initData){lock("openTelegram","openTelegramSub");return;}
  try{const data=await api("me");state.me=data.viewer;state.lang=COPY[data.viewer.lang]?data.viewer.lang:"en";state.dealers=data.dealers;translateChrome();document.getElementById("nav").hidden=false;document.getElementById("refresh").hidden=false;await load();}
  catch(error){lock(error.status===403?"denied":error.status===401?"expiredSession":"error",error.status===403?"deniedSub":error.status===401?"expiredSessionSub":"errorSub",error.status!==401&&error.status!==403);}
}
boot();
