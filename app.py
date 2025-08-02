"""Main Flask application for the vocabulary trainer.

This module wires together the database, importer, translator and quiz
logic into a simple web application. The UI is intentionally kept
minimalistic and self-contained using only built‑in CSS to avoid
external dependencies. The application persists data in an SQLite
database located in the working directory.

To run the app locally:

    $ python -m glosprogram.app

Then open http://127.0.0.1:5000/ in your browser.

Note: For production use you should consider setting a stronger
secret key and running behind a proper WSGI server.
"""

from __future__ import annotations

import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file

from .database import Database
from .importer import Importer
from .quiz import Quiz, Question


def create_app(db_path: str = "glosprogram.db") -> Flask:
    app = Flask(__name__)
    # In a real application you should provide a random secret key via an
    # environment variable. For local development this static key is
    # acceptable.
    app.secret_key = os.environ.get("GLOSPROGRAM_SECRET", "dev-secret-key")

    # Instantiate core components
    db = Database(db_path)
    importer = Importer(db)
    quiz_logic = Quiz(db)

    # User management
    @app.before_request
    def require_user():
        # Skip checking for login page and static assets
        if request.endpoint in {"login", "static"}:
            return
        # Allow first run to create a user; index will redirect to login if no user
        if "user_id" not in session and request.endpoint not in {"login"}:
            return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login() -> str:
        """Select an existing user or create a new one."""
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            if not username:
                flash("Ange ett användarnamn.", "error")
                return redirect(url_for("login"))
            user_id = db.get_user_id(username.title())
            session["user_id"] = user_id
            session["username"] = username.title()
            flash(f"Inloggad som {username.title()}.", "success")
            return redirect(url_for("index"))
        users = db.list_users()
        return render_template("login.html", users=users)

    @app.route("/logout")
    def logout() -> str:
        session.pop("user_id", None)
        session.pop("username", None)
        flash("Du är utloggad.", "success")
        return redirect(url_for("login"))

    @app.route("/")
    def index() -> str:
        sets = db.fetch_sets()
        return render_template("index.html", sets=sets)

    @app.route("/import", methods=["GET", "POST"])
    def import_view() -> str:
        if request.method == "POST":
            set_name = request.form.get("set_name", "New Lesson").strip()
            text = request.form.get("vocab_text", "").strip()
            uploaded_file = request.files.get("file")
            languages_raw = request.form.get("languages_order", "").strip()
            languages_order = (
                [s.strip() for s in languages_raw.split(",") if s.strip()]
                if languages_raw
                else None
            )
            tags_raw = request.form.get("tags", "").strip()
            tags = (
                [t.strip() for t in tags_raw.split(",") if t.strip()]
                if tags_raw
                else None
            )
            auto_spanish = request.form.get("auto_spanish") == "on"
            # If a file is uploaded, parse its contents into text and language order
            if uploaded_file and uploaded_file.filename:
                filename = uploaded_file.filename.lower()
                content = uploaded_file.read()
                try:
                    from io import BytesIO
                    import pandas as pd
                    if filename.endswith(".xlsx") or filename.endswith(".xls"):
                        df = pd.read_excel(BytesIO(content))
                    else:
                        # Assume CSV with comma delimiter
                        df = pd.read_csv(BytesIO(content))
                except Exception as e:
                    flash(f"Kunde inte läsa filen: {e}", "error")
                    return redirect(url_for("import_view"))
                # Use header as languages order
                languages_order = list(df.columns)
                # Build text string from dataframe values
                rows = df.fillna("").astype(str).values.tolist()
                text = "\n".join(["\t".join(row) for row in rows])
            if not text:
                flash("Please paste or upload some vocabulary lines.", "error")
                return redirect(url_for("import_view"))
            set_id = importer.import_from_string(
                set_name,
                text,
                languages_order=languages_order,
                auto_translate_spanish=auto_spanish,
                tags=tags,
            )
            flash(f"Imported {set_name} (ID {set_id}).", "success")
            return redirect(url_for("index"))
        else:
            languages = db.list_languages()
            return render_template("import.html", languages=languages)

    @app.route("/add/<int:set_id>", methods=["GET", "POST"])
    def add_words(set_id: int) -> str:
        """Manually add vocabulary to an existing set.

        This page allows the user to append new words to an existing lesson.
        The interface is similar to the import page but does not create
        a new set. The user can specify languages order and optionally
        request Spanish translation. Upon submission, the importer will
        parse the input and populate the selected set.
        """
        # Ensure set exists
        sets = [s for s in db.fetch_sets() if s["id"] == set_id]
        if not sets:
            flash("Set not found.", "error")
            return redirect(url_for("index"))
        if request.method == "POST":
            text = request.form.get("vocab_text", "").strip()
            languages_raw = request.form.get("languages_order", "").strip()
            languages_order = (
                [s.strip() for s in languages_raw.split(",") if s.strip()]
                if languages_raw
                else None
            )
            auto_spanish = request.form.get("auto_spanish") == "on"
            if not text:
                flash("Fyll i glosor att lägga till.", "error")
                return redirect(url_for("add_words", set_id=set_id))
            # Use importer to add to existing set
            importer.import_into_set(
                set_id,
                text,
                languages_order=languages_order,
                auto_translate_spanish=auto_spanish,
            )
            flash("Ord tillagda.", "success")
            return redirect(url_for("index"))
        else:
            # Provide list of languages for convenience
            languages = db.list_languages()
            set_name = sets[0]["name"]
            return render_template("add.html", set_id=set_id, set_name=set_name, languages=languages)

    @app.route("/quiz/<int:set_id>", methods=["GET", "POST"])
    def quiz_start(set_id: int) -> str:
        # Ensure the set exists
        sets = [s for s in db.fetch_sets() if s["id"] == set_id]
        if not sets:
            flash("Set not found.", "error")
            return redirect(url_for("index"))
        if request.method == "POST":
            # Start a new quiz session
            selected_langs = request.form.getlist("languages")
            if selected_langs:
                # Only allow two languages at a time
                selected_langs = selected_langs[:2]
            else:
                selected_langs = None
            random_dir = request.form.get("random_direction") == "on"
            # Quiz mode: typed, choice, flashcard
            mode = request.form.get("mode", "typed")
            # Determine group restrictions based on spaced repetition or problematic words
            problem_only = request.form.get("problem_only") == "on"
            spaced = request.form.get("spaced_repetition") == "on"
            allowed: Optional[list[int]] = None
            # Compute allowed group ids for spaced repetition and/or problematic words
            if spaced:
                uid = session.get("user_id")
                if uid:
                    due = db.get_due_groups(set_id, uid)
                else:
                    due = None
                if problem_only:
                    prob = db.get_problematic_groups(set_id)
                    # intersection of due and problematic
                    if due is not None:
                        allowed = [gid for gid in due if gid in prob]
                    else:
                        allowed = prob
                else:
                    allowed = due
            elif problem_only:
                allowed = db.get_problematic_groups(set_id)
            # Generate first question
            q = quiz_logic.generate_question(
                set_id,
                selected_langs,
                random_dir,
                allowed_group_ids=allowed,
            )
            if not q:
                flash("No valid questions available.", "error")
                return redirect(url_for("index"))
            # Store quiz state in session
            session["quiz"] = {
                "set_id": set_id,
                "selected_langs": selected_langs,
                "random_dir": random_dir,
                "mode": mode,
                "spaced": spaced,
                "problem_only": problem_only,
                "allowed_groups": allowed,
                "score": 0,
                "total": 0,
                "answers": [],  # keep track of each answer for statistics
            }
            # Save current question including group id
            session["current_question"] = {
                "group_id": q.group_id,
                "src_lang": q.source_language,
                "src_word": q.source_word,
                "tgt_lang": q.target_language,
                "tgt_word": q.target_word,
                "choices": None,  # will be populated for multiple-choice
            }
            # For multiple-choice mode, pre-generate distractors
            if mode == "choice":
                distractors = db.get_distractors(set_id, q.group_id, q.target_language, num_choices=3)
                # include the correct answer and shuffle
                options = distractors + [q.target_word]
                import random
                random.shuffle(options)
                session["current_question"]["choices"] = options
            return redirect(url_for("quiz_question", set_id=set_id))
        else:
            languages = db.list_languages()
            return render_template("quiz_start.html", set_id=set_id, languages=languages)

    @app.route("/quiz/<int:set_id>/question", methods=["GET", "POST"])
    def quiz_question(set_id: int) -> str:
        quiz_state = session.get("quiz")
        if not quiz_state or quiz_state.get("set_id") != set_id:
            flash("Quiz session expired or invalid.", "error")
            return redirect(url_for("quiz_start", set_id=set_id))
        current_q = session.get("current_question")
        if request.method == "POST":
            # Determine quiz mode
            mode = quiz_state.get("mode", "typed")
            # Evaluate the submitted answer based on mode
            user_input = None
            correct = False
            if mode == "typed":
                user_input = request.form.get("answer", "").strip()
                if current_q:
                    correct = user_input.lower() == current_q["tgt_word"].lower()
            elif mode == "choice":
                user_input = request.form.get("choice", "").strip()
                if current_q:
                    # correct if selected choice matches the target word
                    correct = user_input == current_q["tgt_word"]
            elif mode == "flashcard":
                user_input = request.form.get("knew", "no")  # 'yes' if user knew it
                if current_q:
                    correct = user_input == "yes"
            # Append to answers log
            if current_q:
                quiz_state.setdefault("answers", []).append({
                    "group_id": current_q.get("group_id"),
                    "from_lang": current_q.get("src_lang"),
                    "to_lang": current_q.get("tgt_lang"),
                    "correct": correct,
                })
                # Update spaced repetition progress per user
                uid = session.get("user_id")
                if uid:
                    db.update_user_progress(uid, current_q.get("group_id"), correct)
            # Update counters
            quiz_state["total"] += 1
            if correct:
                quiz_state["score"] += 1
            # Update allowed groups if spaced repetition is active
            if quiz_state.get("spaced"):
                uid = session.get("user_id")
                due_groups = db.get_due_groups(set_id, uid) if uid else None
                allowed = None
                if quiz_state.get("problem_only"):
                    prob = db.get_problematic_groups(set_id)
                    if due_groups is not None:
                        allowed = [gid for gid in due_groups if gid in prob]
                    else:
                        allowed = prob
                else:
                    allowed = due_groups
                # If empty list, treat as None (means no restriction)
                if allowed is not None and len(allowed) == 0:
                    allowed = None
                quiz_state["allowed_groups"] = allowed
            session["quiz"] = quiz_state
            # Generate next question
            q = quiz_logic.generate_question(
                set_id,
                languages=quiz_state.get("selected_langs"),
                random_direction=quiz_state.get("random_dir", False),
                allowed_group_ids=quiz_state.get("allowed_groups"),
            )
            if q is None:
                # Quiz finished: persist results
                score = quiz_state["score"]
                total = quiz_state["total"]
                user_id = session.get("user_id")
                session_id = db.record_quiz_session(set_id, total, score, user_id)
                for ans in quiz_state.get("answers", []):
                    db.record_quiz_answer(
                        session_id,
                        ans.get("group_id"),
                        ans.get("from_lang"),
                        ans.get("to_lang"),
                        ans.get("correct"),
                    )
                session.pop("quiz", None)
                session.pop("current_question", None)
                return render_template(
                    "quiz_result.html",
                    set_id=set_id,
                    score=score,
                    total=total,
                )
            # Save new question into session
            session["current_question"] = {
                "group_id": q.group_id,
                "src_lang": q.source_language,
                "src_word": q.source_word,
                "tgt_lang": q.target_language,
                "tgt_word": q.target_word,
                "choices": None,
            }
            # For multiple-choice mode, generate distractors
            if quiz_state.get("mode") == "choice":
                distractors = db.get_distractors(set_id, q.group_id, q.target_language, num_choices=3)
                options = distractors + [q.target_word]
                import random
                random.shuffle(options)
                session["current_question"]["choices"] = options
            # Render next question page with feedback
            return render_template(
                "quiz_question.html",
                set_id=set_id,
                question=session["current_question"],
                feedback=correct,
                previous_answer=user_input,
                mode=quiz_state.get("mode"),
            )
        else:
            # GET: present current question
            if not current_q:
                flash("No current question.", "error")
                return redirect(url_for("quiz_start", set_id=set_id))
            return render_template(
                "quiz_question.html",
                set_id=set_id,
                question=current_q,
                feedback=None,
                previous_answer=None,
                mode=quiz_state.get("mode"),
            )

    @app.route("/stats/<int:set_id>")
    def stats(set_id: int) -> str:
        """Show statistics and problematic words for a lesson."""
        # Determine interval in days from query param
        interval = request.args.get("interval")
        if interval == "7":
            days = 7
        elif interval == "30":
            days = 30
        else:
            days = None
        # Determine user filter: default to current user unless ?user=all
        user_param = request.args.get("user")
        if user_param == "all":
            uid = None
        else:
            uid = session.get("user_id")
        # Fetch stats
        stats_data = db.get_stats(set_id, since_days=days, user_id=uid)
        # Fetch problematic groups and their words
        prob_ids = db.get_problematic_groups(set_id, threshold=0.7, since_days=days, user_id=uid)
        problem_words = []
        for gid in prob_ids:
            problem_words.append(db.fetch_group_words(gid))
        # Determine set name for display
        sets = [s for s in db.fetch_sets() if s["id"] == set_id]
        set_name = sets[0]["name"] if sets else "Okänd"
        return render_template(
            "stats.html",
            set_id=set_id,
            set_name=set_name,
            stats=stats_data,
            problem_words=problem_words,
            interval=interval,
        )

    @app.route("/export/<int:set_id>")
    def export_set(set_id: int):
        """Export a lesson's vocabulary to CSV or Excel.

        The default format is CSV. Specify ?format=excel to receive an
        Excel file (.xlsx). The exported file contains one row per
        translation group and one column per language present in the set.
        Unknown languages are included as columns.
        """
        fmt = request.args.get("format", "csv").lower()
        # Gather all group words and determine language columns
        group_ids = db.fetch_set_group_ids(set_id)
        if not group_ids:
            flash("Set saknar ord att exportera.", "error")
            return redirect(url_for("index"))
        all_languages = set()
        rows = []
        for gid in group_ids:
            words = db.fetch_group_words(gid)
            all_languages.update(words.keys())
            rows.append(words)
        # Sort languages for column ordering
        langs = sorted(all_languages)
        # Build list of dicts with all languages present
        data = []
        for entry in rows:
            row = {lang: entry.get(lang, "") for lang in langs}
            data.append(row)
        try:
            import pandas as pd
        except Exception as e:
            flash(f"Pandas behövs för export: {e}", "error")
            return redirect(url_for("index"))
        df = pd.DataFrame(data)
        from io import BytesIO
        buffer = BytesIO()
        if fmt in {"excel", "xlsx", "xls"}:
            try:
                df.to_excel(buffer, index=False)
            except Exception as e:
                flash(f"Kunde inte exportera till Excel: {e}", "error")
                return redirect(url_for("index"))
            buffer.seek(0)
            filename = f"set_{set_id}.xlsx"
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            # default to CSV
            df.to_csv(buffer, index=False)
            buffer.seek(0)
            filename = f"set_{set_id}.csv"
            mimetype = "text/csv"
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype=mimetype)

    return app


if __name__ == "__main__":
    # Only executed when running `python -m glosprogram.app`
    app = create_app()
    # Run with debug=True for live reload during development
    app.run(debug=True)