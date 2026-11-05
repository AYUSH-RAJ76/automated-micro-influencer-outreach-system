import json
import sys
from pathlib import Path

# Streamlit may use the script directory as its import root. Ensure the project
# package is importable when launched with `streamlit run dashboard/streamlit_app.py`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st
from sqlalchemy import select, func
from app.db.database import Base, engine, SessionLocal
from app.db.models import Influencer, GeneratedMessage, OutreachAttempt, Campaign, OutreachEvent, Approval
from app.services.discovery import run_discovery
from app.services.filtering import save_decision
from app.services.enrichment import enrich_influencer
from app.services.personalization import generate
from app.services.sending import send_approved, send_test_email
from app.services.enrichment import valid_email
from app.services.export import influencers_frame, messages_frame, outreach_frame
from app.services.tracking import mark_dm, record_outreach_event
from app.core.config import get_settings

Base.metadata.create_all(bind=engine)
st.set_page_config(page_title="Creator Outreach",page_icon="✦",layout="wide")
st.markdown("<style>.block-container{padding-top:2rem} [data-testid=stMetric]{background:#101c2a;padding:18px;border-radius:14px;border:1px solid #24364a}</style>",unsafe_allow_html=True)
with SessionLocal() as db:
    st.sidebar.title("✦ Creator Outreach")
    page=st.sidebar.radio("Workspace",["Overview","Discovery","Influencer Dataset","Enrichment","AI Outreach","Campaign Sending","Outreach Tracker","Export"])
    settings=get_settings()
    st.sidebar.caption(f"YouTube API: {'Configured' if settings.youtube_api_key else 'Missing'} · Groq: {'Configured' if settings.groq_api_key else 'Missing'}")
    total=db.scalar(select(func.count(Influencer.id))) or 0
    qualified=db.scalar(select(func.count(Influencer.id)).where(Influencer.filter_status=="PASS")) or 0
    failed=db.scalar(select(func.count(Influencer.id)).where(Influencer.filter_status=="FAIL")) or 0
    review=db.scalar(select(func.count(Influencer.id)).where(Influencer.filter_status=="NEEDS_REVIEW")) or 0
    if page=="Overview":
        st.title("Creator outreach overview");st.caption("Evidence based YouTube discovery, human reviewed before any outreach.")
        cols=st.columns(4)
        for col,label,val in zip(cols,["Discovered","Qualified","Failed","Needs review"],[total,qualified,failed,review]):col.metric(label,val)
        cols=st.columns(4);cols[0].metric("Public emails found",db.scalar(select(func.count(Influencer.id)).where(Influencer.email!="Not Found")) or 0);cols[1].metric("Messages",db.scalar(select(func.count(GeneratedMessage.id))) or 0);cols[2].metric("Approved",db.scalar(select(func.count(GeneratedMessage.id)).where(GeneratedMessage.status=="APPROVED")) or 0);cols[3].metric("Simulated / sent",db.scalar(select(func.count(OutreachAttempt.id)).where(OutreachAttempt.status.in_(["SIMULATED","SENT"]))) or 0)
        st.info("A hidden subscriber count, unavailable engagement metrics, or absent public contact details are represented as unknown or Not Found. No sample influencer identities are inserted.")
    elif page=="Discovery":
        st.title("Discover YouTube channels")
        categories=["Technology","Fintech","Gaming","Beauty","Fitness","Fashion","Travel","Food","Education","Health & Wellness","Personal Finance","Other"]
        st.session_state.setdefault("discovery_category_value", "Technology")
        st.session_state.setdefault("discovery_custom_category_value", "")
        st.session_state.setdefault("discovery_target_value", 50)
        def _save_discovery_category():
            st.session_state["discovery_category_value"]=st.session_state["_discovery_category_widget"]
        def _save_discovery_custom_category():
            st.session_state["discovery_custom_category_value"]=st.session_state["_discovery_custom_category_widget"]
        def _save_discovery_target():
            st.session_state["discovery_target_value"]=st.session_state["_discovery_target_widget"]
        category=st.selectbox("Influencer category",categories,index=categories.index(st.session_state["discovery_category_value"]) if st.session_state["discovery_category_value"] in categories else 0,key="_discovery_category_widget",on_change=_save_discovery_category)
        if category=="Other":
            niche=st.text_input("Enter a category",value=st.session_state["discovery_custom_category_value"],placeholder="e.g. sustainable home design",key="_discovery_custom_category_widget",on_change=_save_discovery_custom_category)
        else:
            niche=category
        target=st.slider("Target channels",1,100,value=st.session_state["discovery_target_value"],key="_discovery_target_widget",on_change=_save_discovery_target)
        refresh_search=st.checkbox("Refresh YouTube search results (uses API quota)",value=False,help="Bypass the 30-minute search cache and request current results from YouTube. Each search.list request costs 100 quota units.")
        last_run = st.session_state.get("last_discovery_result")
        if last_run:
            st.subheader("Most recent discovery run")
            if last_run.get("success"):
                st.info(f"Previous run: niche={last_run.get('niche', 'Technology')}, requested={last_run.get('target', 50)}. Found {last_run.get('discovered', 0)} unique channel IDs and stored or updated {last_run.get('stored', 0)} channels. {last_run.get('qualified_this_run', 0)} qualified in that run. Current dataset: {last_run.get('total_in_database', 0)} channels, {last_run.get('total_qualified', 0)} PASS.")
            else:
                st.warning("The last discovery run did not complete any search successfully. Previously saved records are unchanged.")
            if last_run.get("quota_exhausted"):
                st.error("YouTube daily API quota was exhausted during that run.")
            if last_run.get("queries"):
                st.dataframe(pd.DataFrame(last_run["queries"]), width="stretch", hide_index=True)
            if last_run.get("channels"):
                st.dataframe(pd.DataFrame(last_run["channels"]), width="stretch", hide_index=True)
            st.caption("Discovery results are saved in the database. The button below is ready to start another run.")
        if st.button("Start real discovery",type="primary"):
            if not settings.youtube_api_key:st.error("Set YOUTUBE_API_KEY in .env and restart the dashboard.")
            else:
                # Remove the previous result so it cannot be mistaken for this run.
                st.session_state.pop("last_discovery_result", None)
                with st.spinner("Calling YouTube Data API v3. This may take a few minutes..."):
                    try:
                        result=run_discovery(db,target,niche,refresh_search_cache=refresh_search)
                        # Keep the dashboard compatible with a still-running process
                        # that may have imported an older discovery module.
                        result.setdefault("niche",niche)
                        result.setdefault("target",target)
                        result.setdefault("queries",[])
                        result.setdefault("channels",[])
                        result.setdefault("errors",[])
                        result.setdefault("qualified_this_run",result.get("qualified",0))
                        result.setdefault("total_qualified",db.scalar(select(func.count(Influencer.id)).where(Influencer.filter_status=="PASS")) or 0)
                        result.setdefault("total_in_database",db.scalar(select(func.count(Influencer.id))) or 0)
                        st.session_state["last_discovery_result"] = result
                        discovery_succeeded=result.get("success",bool(result.get("queries")))
                        if discovery_succeeded:
                            st.success(f"This run: niche={result.get('niche',niche)}, requested={result.get('target',target)}; found {result.get('discovered',0)} unique channel IDs and stored or updated {result.get('stored',0)} channels. {result.get('qualified_this_run',0)} qualified. Dataset total: {result.get('total_in_database',0)} channels, {result.get('total_qualified',0)} PASS.")
                        else:
                            st.error("Discovery failed: every YouTube search query failed. No success was reported.")
                        if result.get("quota_exhausted"):
                            st.error("YouTube daily API quota is exhausted. Existing and partially discovered records were preserved.")
                        if result.get("api_usage"):
                            usage=result["api_usage"]
                            st.caption(f"API requests: {usage.get('request_count', 'n/a')} · Estimated quota units: {usage.get('quota_units_estimated', 'n/a')} · Retries: {usage.get('retries', 'n/a')}")
                        if result.get("queries"):
                            st.dataframe(pd.DataFrame(result.get("queries",[])),width="stretch",hide_index=True)
                        if result.get("channels"):
                            st.dataframe(pd.DataFrame(result["channels"]),width="stretch",hide_index=True)
                        if result.get("errors"):st.warning("Some discovery steps failed: "+" | ".join(result.get("errors",[])[:5]))
                    except Exception as e:st.error(f"YouTube discovery could not complete: {e}")
        st.caption("Searches are sequential and bounded. Quota exhaustion is reported directly; discovery counts are never filled with demo records.")
    elif page=="Influencer Dataset":
        st.title("Influencer dataset");st.caption("Real YouTube channels saved from discovery, grouped by the category you searched. Newest fetched profiles appear first.")
        status=st.selectbox("Filter status",["All","PASS","FAIL","NEEDS_REVIEW"]);query=st.text_input("Search influencer name")
        niches=db.scalars(select(Influencer.niche).distinct().order_by(Influencer.niche)).all()
        selected_niche=st.selectbox("Filter niche",["All",*niches])
        q=select(Influencer)
        if status!="All":q=q.where(Influencer.filter_status==status)
        if selected_niche!="All":q=q.where(Influencer.niche==selected_niche)
        if query:q=q.where(Influencer.name.ilike(f"%{query}%"))
        rows=db.execute(q.order_by(Influencer.collected_at.desc(),Influencer.id.desc())).scalars().all()
        st.caption(f"{len(rows)} saved channel(s) match these filters.")
        st.dataframe(pd.DataFrame([{"Influencer name":i.name,"Category searched":i.niche,"Subscribers":i.subscribers,"Qualification":i.filter_status,"Profile":i.profile_url,"Last fetched":i.collected_at} for i in rows]),width="stretch",hide_index=True)
        for i in rows:
            with st.expander(f"{i.name} · {i.filter_status}"):
                st.markdown(f"[Open {i.platform} profile]({i.profile_url})");st.write(i.description);st.write("Reasons:",i.reason_text);st.write("Recent content:",", ".join(v.title for v in i.videos[:5]));i.do_not_contact=st.checkbox("Do not contact",value=i.do_not_contact,key=f"dnc{i.id}")
        db.commit()
    elif page=="Enrichment":
        st.title("Public contact details for PASS channels")
        st.caption("Checks each qualified channel's public YouTube description for a published email and contact links. It does not reveal private emails or scrape external sites.")
        rows=db.execute(select(Influencer).where(Influencer.filter_status=="PASS").order_by(Influencer.collected_at.desc())).scalars().all()
        if st.button("Enrich qualified channels"):
            for i in rows:enrich_influencer(db,i)
            db.commit();st.success(f"Enriched {len(rows)} qualified profiles using channel description and fetched video metadata.")
        st.dataframe(pd.DataFrame([{"Name":i.name,"Category":i.niche,"Public email":i.email,"Email source":i.email_source,"Website / first public link":i.website,"Themes":i.themes} for i in rows]),width="stretch",hide_index=True)
        for i in rows:
            try: contact_links=json.loads(i.data_flags or "{}").get("public_contact_links",[])
            except (TypeError,ValueError): contact_links=[]
            with st.expander(f"Contact options · {i.name}"):
                st.markdown(f"[Open YouTube channel About page]({i.profile_url}/about)")
                st.write(f"Public email: {i.email}")
                if contact_links:
                    for link in contact_links:st.markdown(f"[Public link]({link})")
                else:st.info("No email or external contact link was found in the public channel description. Check the channel's About page for a business inquiry email or linked website.")
    elif page=="AI Outreach":
        st.title("AI outreach review queue");st.caption("Only channels currently qualified as PASS in your database can be selected. Review and edit each message before approval.")
        candidates=db.execute(select(Influencer).where(Influencer.filter_status=="PASS").order_by(Influencer.collected_at.desc(),Influencer.id.desc())).scalars().all()
        candidate_names={influencer.id:influencer for influencer in candidates}
        if candidates:
            st.caption(f"{len(candidates)} currently qualified channel(s) available.")
            # Reset widget state if the set of currently qualified IDs changes;
            # this prevents previously selected, now-unqualified rows lingering.
            selection_key=f"gen_ids_v3_{hash(tuple(candidate_names))}"
            selected_ids=st.multiselect(
                "Qualified YouTube channels",
                options=list(candidate_names),
                format_func=lambda influencer_id:(
                    f"{candidate_names[influencer_id].name} · "
                    f"{candidate_names[influencer_id].niche} · "
                    f"{candidate_names[influencer_id].subscribers if candidate_names[influencer_id].subscribers is not None else 'subscribers unknown'} · "
                    f"ID {influencer_id}"
                ),
                key=selection_key,
            )
        else:
            selected_ids=[]
            st.info("No currently qualified channels are available. Run discovery, then review the Influencer Dataset for channels with PASS status.")
        if st.button("Generate messages"):
            if not selected_ids:
                st.warning("Select at least one qualified channel before generating messages.")
            elif not get_settings().groq_api_key:
                st.error("GROQ_API_KEY is not configured. Add it to .env and restart Streamlit.")
            else:
                progress=st.progress(0.0)
                failures=[]
                with st.status("Connecting to Groq…",expanded=True) as generation_status:
                    for index,influencer_id in enumerate(selected_ids, start=1):
                        # Streamlit widget state can outlive the SQLAlchemy session
                        # that produced a widget option. Keep only IDs in state and
                        # reload the row under this run's active session.
                        influencer=db.get(Influencer,int(influencer_id))
                        if influencer is None:
                            failures.append(f"Influencer {influencer_id} no longer exists.")
                            st.error(f"Could not load selected influencer {influencer_id}; refresh the page and select again.")
                            progress.progress(index/len(selected_ids))
                            continue
                        if influencer.filter_status!="PASS":
                            failures.append(f"{influencer.name} is no longer qualified (status: {influencer.filter_status}).")
                            st.warning(f"Skipped {influencer.name}: it is no longer qualified. Refresh the page and select current PASS channels.")
                            progress.progress(index/len(selected_ids))
                            continue
                        generation_status.update(label=f"Generating {index}/{len(selected_ids)}: {influencer.name}")
                        try:
                            data,context=generate(influencer)
                            db.add(GeneratedMessage(influencer_id=influencer.id,subject=data["subject"],email_body=data["email_body"],dm_body=data["dm_body"],model_name=get_settings().groq_model,source_context=json.dumps(context)))
                            db.commit()
                            st.write(f"✓ Draft saved for {influencer.name}")
                        except Exception as e:
                            db.rollback()
                            failures.append(f"{influencer.name}: {e}")
                            st.error(f"Could not generate for {influencer.name}: {e}")
                        progress.progress(index/len(selected_ids))
                    generation_status.update(label="Generation finished" if not failures else "Generation finished with errors",state="complete" if not failures else "error",expanded=True)
                if failures:
                    st.warning(f"{len(failures)} of {len(selected_ids)} message(s) failed. Successful drafts were saved and remain in the review queue.")
        msgs=db.execute(select(GeneratedMessage).order_by(GeneratedMessage.created_at.desc())).scalars().all()
        for m in msgs:
            with st.expander(f"{m.influencer.name} · {m.status}"):
                st.write(f"Profile evidence: {m.influencer.reason_text}; recent titles: {', '.join(v.title for v in m.influencer.videos[:3])}")
                m.subject=st.text_input("Subject",m.subject,key=f"s{m.id}");m.email_body=st.text_area("Email",m.email_body,key=f"e{m.id}");m.dm_body=st.text_area("Instagram DM",m.dm_body,key=f"d{m.id}")
                a,b,c=st.columns(3)
                if a.button("Save edits",key=f"save{m.id}"):m.status="DRAFT";db.commit();st.success("Saved")
                if b.button("Approve",key=f"approve{m.id}"):
                    if m.influencer.do_not_contact:st.error("This channel is marked do not contact.")
                    else:m.status="APPROVED";m.approved_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc);db.add(Approval(message_id=m.id,decision="APPROVED"));db.commit();st.success("Approved")
                if c.button("Reject",key=f"reject{m.id}"):m.status="REJECTED";db.add(Approval(message_id=m.id,decision="REJECTED"));db.commit();st.warning("Rejected")
                st.code(m.dm_body);st.caption("Use the Copy button above or select the DM text to copy for manual Instagram sending. Automated Instagram messaging is not supported.")
    elif page=="Campaign Sending":
        st.title("Campaign sending");st.info("Bulk sending requires approved messages, a public email address, no do-not-contact flag, and live-send confirmation. A one-off test email is sent only to the address entered below.")
        if st.button("Reload .env settings",help="Reload the effective SMTP and DRY_RUN settings for this Streamlit process."):
            get_settings.cache_clear()
            st.rerun()
        smtp_settings=get_settings()
        st.subheader("Send one test email")
        if not all((smtp_settings.smtp_host,smtp_settings.smtp_user,smtp_settings.smtp_password,smtp_settings.smtp_from)):
            st.warning("SMTP settings are incomplete. Configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD, and SMTP_FROM before sending.")
        with st.form("smtp_test_email"):
            test_recipient=st.text_input("Test recipient",value=smtp_settings.smtp_from,help="Defaults to SMTP_FROM. This is a neutral test and does not include influencer data.")
            confirm_test=st.checkbox("Send one real test email to this address")
            submit_test=st.form_submit_button("Send test email")
        if submit_test:
            try:
                test_result=send_test_email(test_recipient,live_confirmed=confirm_test)
                if test_result["status"]=="SENT":st.success(test_result["message"])
                else:st.info(test_result["message"])
            except (ValueError,RuntimeError) as exc:st.error(str(exc))
        with st.form("campaign"):
            name=st.text_input("Campaign name", "Developer education outreach");brand=st.text_input("Brand",get_settings().brand_name);create=st.form_submit_button("Create campaign")
        if create:c=Campaign(name=name,brand_name=brand);db.add(c);db.commit();st.success(f"Campaign created: {c.id}")
        campaigns=db.execute(select(Campaign).order_by(Campaign.id.desc())).scalars().all();c=st.selectbox("Campaign",campaigns,format_func=lambda x:f"{x.name} · #{x.id}") if campaigns else None
        approved=db.execute(select(GeneratedMessage).where(GeneratedMessage.status=="APPROVED").order_by(GeneratedMessage.created_at.desc())).scalars().all()
        eligible=[m for m in approved if not m.influencer.do_not_contact and valid_email(m.influencer.email)]
        drafts=db.execute(select(GeneratedMessage).where(GeneratedMessage.status=="DRAFT")).scalars().all()
        chosen=st.multiselect("Sendable approved messages",eligible,format_func=lambda m:f"{m.influencer.name} · {m.influencer.email}")
        skipped_count=len(approved)-len(eligible)
        if drafts:
            st.info(f"{len(drafts)} generated draft(s) are waiting for review. Open AI Outreach, review each message, and click Approve; approved messages with a public email will then appear here.")
        if skipped_count:
            blocked_dnc=sum(bool(m.influencer.do_not_contact) for m in approved)
            blocked_email=sum(not valid_email(m.influencer.email) for m in approved)
            st.caption(f"{skipped_count} approved message(s) are not sendable: {blocked_email} missing/invalid public email; {blocked_dnc} marked do not contact.")
            with st.expander("Review blocked approved messages"):
                for message in approved:
                    influencer=message.influencer
                    if not influencer.do_not_contact and valid_email(influencer.email):
                        continue
                    st.markdown(f"**{influencer.name}** · {message.status} · current email: `{influencer.email or 'Not Found'}`")
                    st.markdown(f"[Open YouTube About page]({influencer.profile_url}/about)")
                    if influencer.do_not_contact:
                        st.warning("This creator is marked Do Not Contact and cannot be sent a campaign email.")
                        continue
                    with st.form(f"verified_contact_{message.id}"):
                        public_email=st.text_input("Public business email you verified",key=f"public_email_{message.id}")
                        source_url=st.text_input("Public page where you found this email",value=f"{influencer.profile_url}/about",key=f"email_source_{message.id}")
                        verified=st.checkbox("I verified this business email is publicly listed by the creator",key=f"email_verified_{message.id}")
                        save_contact=st.form_submit_button("Save verified contact")
                    if save_contact:
                        if not valid_email(public_email):st.error("Enter a valid email address.")
                        elif not source_url.strip().startswith("https://"):st.error("Enter the HTTPS URL where you verified the contact.")
                        elif not verified:st.error("Confirm that you verified the address is publicly listed.")
                        else:
                            influencer.email=public_email.strip().lower()
                            influencer.email_source=f"Manual public verification: {source_url.strip()}"
                            db.commit()
                            st.success("Verified contact saved. It will appear in Sendable approved messages.")
                            st.rerun()
        if not drafts and not approved:
            st.info("No generated messages yet. Select qualified channels in AI Outreach and generate drafts first.")
        if c:
            live=not get_settings().dry_run
            st.caption(f"Effective DRY_RUN setting: {str(get_settings().dry_run).lower()}")
            if not live:st.warning("DRY_RUN is enabled. Set exactly DRY_RUN=false in .env, then click Reload .env settings (or restart Streamlit). An operating-system DRY_RUN=true variable overrides .env.")
            confirm=st.checkbox("I confirm sending this selected batch now",disabled=not live)
            if st.button("Send selected messages",type="primary"):
                if not chosen:st.warning("Select an approved, sendable message first. Drafts must be approved in AI Outreach.")
                elif live and not confirm:st.error("Confirm the live batch before sending.")
                else:
                    try:st.write(send_approved(db,c,[m.id for m in chosen],live_confirmed=confirm))
                    except Exception as e:st.error(str(e))
    elif page=="Outreach Tracker":
        st.title("Outreach tracker")
        st.caption("One history for campaign email attempts and manually recorded Instagram DMs, replies, and follow-ups.")
        attempts=db.execute(select(OutreachAttempt).order_by(OutreachAttempt.id.desc())).scalars().all()
        duplicate_send_event=(OutreachEvent.channel=="email") & (OutreachEvent.status.in_(["SENDING","SENT","SIMULATED","FAILED"]))
        events=db.execute(select(OutreachEvent).where(OutreachEvent.channel.in_(["instagram_dm","email"]),~duplicate_send_event).order_by(OutreachEvent.created_at.desc())).scalars().all()
        timeline=[]
        for attempt in attempts:
            influencer=db.get(Influencer,attempt.influencer_id)
            campaign=db.get(Campaign,attempt.campaign_id)
            timeline.append({"Creator":influencer.name if influencer else f"Influencer {attempt.influencer_id}","Channel":"Email","Campaign":campaign.name if campaign else "—","Contact":attempt.normalized_email,"Status":attempt.status,"Date":attempt.attempted_at,"Details":attempt.error or attempt.provider_response or "—"})
        for event in events:
            influencer=db.get(Influencer,event.influencer_id)
            campaign=db.get(Campaign,event.campaign_id) if event.campaign_id else None
            timeline.append({"Creator":influencer.name if influencer else f"Influencer {event.influencer_id}","Channel":"Instagram DM" if event.channel=="instagram_dm" else "Email follow-up","Campaign":campaign.name if campaign else "—","Contact":"Manual","Status":event.status,"Date":event.created_at,"Details":event.note or "—"})
        if timeline:
            st.subheader("Activity summary")
            metrics=st.columns(5)
            metrics[0].metric("Email accepted",sum(row["Channel"]=="Email" and row["Status"]=="SENT" for row in timeline))
            metrics[1].metric("Simulated",sum(row["Channel"]=="Email" and row["Status"]=="SIMULATED" for row in timeline))
            metrics[2].metric("Email failures",sum(row["Channel"]=="Email" and row["Status"]=="FAILED" for row in timeline))
            metrics[3].metric("Instagram DMs sent",sum(row["Channel"]=="Instagram DM" and row["Status"]=="SENT" for row in timeline))
            metrics[4].metric("Replies / follow-ups",sum(row["Status"] in {"REPLIED","FOLLOW_UP"} for row in timeline))
            channel_filter=st.selectbox("Channel filter",["All","Email","Instagram DM","Email follow-up"])
            status_options=["All",*sorted({row["Status"] for row in timeline})]
            status_filter=st.selectbox("Status filter",status_options)
            visible=[row for row in timeline if (channel_filter=="All" or row["Channel"]==channel_filter) and (status_filter=="All" or row["Status"]==status_filter)]
            st.dataframe(pd.DataFrame(visible).sort_values("Date",ascending=False),width="stretch",hide_index=True)
        else:
            st.info("No outreach activity has been logged yet. Sent or simulated campaign emails and manually recorded DMs will appear here.")

        st.subheader("Record manual outreach or response")
        qualified_creators=db.execute(select(Influencer).where(Influencer.filter_status=="PASS").order_by(Influencer.name)).scalars().all()
        if not qualified_creators:
            st.info("No PASS channels are available to track. Qualify channels in Discovery first.")
        else:
            creator_by_id={creator.id:creator for creator in qualified_creators}
            creator_id=st.selectbox("Qualified creator",list(creator_by_id),format_func=lambda creator_id:f"{creator_by_id[creator_id].name} · {creator_by_id[creator_id].niche}")
            creator=creator_by_id[creator_id]
            latest_message=db.execute(select(GeneratedMessage).where(GeneratedMessage.influencer_id==creator_id).order_by(GeneratedMessage.created_at.desc())).scalars().first()
            if latest_message and latest_message.dm_body:
                st.caption("Latest Instagram DM draft — copy and send it manually in Instagram:")
                st.code(latest_message.dm_body)
            channel=st.selectbox("Activity channel",["Instagram DM","Email"],key="tracker_channel")
            activity_options=["SENT","REPLIED","FOLLOW_UP","SKIPPED"] if channel=="Instagram DM" else ["REPLIED","FOLLOW_UP","SKIPPED"]
            activity=st.selectbox("Activity",activity_options,key="tracker_activity")
            campaigns=db.execute(select(Campaign).order_by(Campaign.id.desc())).scalars().all()
            campaign_choices={"No campaign":None,**{f"{campaign.name} · #{campaign.id}":campaign.id for campaign in campaigns}}
            campaign_label=st.selectbox("Campaign (optional)",list(campaign_choices))
            note=st.text_area("Note",placeholder="For example: replied positively; follow up next week")
            confirmed_sent=True
            if channel=="Instagram DM" and activity=="SENT":
                confirmed_sent=st.checkbox("I sent this Instagram DM manually")
            if st.button("Save tracker activity"):
                if not confirmed_sent:
                    st.warning("Confirm the Instagram DM was sent manually before recording it.")
                else:
                    campaign_id=campaign_choices[campaign_label]
                    if channel=="Instagram DM" and activity in {"SENT","SKIPPED"}:
                        mark_dm(db,creator.id,activity,note=note,campaign_id=campaign_id)
                    else:
                        record_outreach_event(db,creator.id,"instagram_dm" if channel=="Instagram DM" else "email",activity,note=note,campaign_id=campaign_id)
                    st.success(f"Recorded {activity} for {creator.name}.")
                    st.rerun()
    elif page=="Export":
        st.title("Export deliverables")
        for label,frame,name in [("Influencer dataset",influencers_frame(db),"influencers.csv"),("Qualified influencers",influencers_frame(db,True),"qualified_influencers.csv"),("Messages",messages_frame(db),"messages.csv"),("Outreach log",outreach_frame(db),"outreach_log.csv")]:
            st.download_button(f"Download {label} CSV",frame.to_csv(index=False),name,"text/csv")
