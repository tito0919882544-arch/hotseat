import os
import secrets
from datetime import datetime

from flask import Flask, request, jsonify, render_template_string
from flask_socketio import SocketIO, join_room, emit


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "hotseat-dev-secret-change-later"
)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet"
)

# -------------------------------------------------
# مؤقتًا: البيانات محفوظة في الذاكرة.
# PostgreSQL سنضيفه في المرحلة التالية.
# -------------------------------------------------

rooms = {}


def create_room_code():
    while True:
        code = secrets.token_hex(3).upper()
        if code not in rooms:
            return code


def get_room(code):
    return rooms.get(code.upper())


def room_state(room):
    return {
        "code": room["code"],
        "host": room["host"],
        "hot_seat": room["hot_seat"],
        "phase": room["phase"],
        "status": room["status"],
        "current_question": room["current_question"],
        "question_number": room["question_number"],
        "questions": room["questions"],
        "participants": list(room["participants"].values()),
    }


# -------------------------------------------------
# الصفحة الرئيسية
# -------------------------------------------------

@app.route("/")
def index():
    return render_template_string(PAGE)


# -------------------------------------------------
# إنشاء غرفة
# -------------------------------------------------

@app.post("/api/rooms")
def create_room():
    data = request.get_json(silent=True) or {}

    host = str(data.get("host", "")).strip()
    hot_seat = str(data.get("hot_seat", "")).strip()

    if not host or not hot_seat:
        return jsonify({
            "error": "يجب إدخال اسم المقدم واسم صاحب الكرسي"
        }), 400

    code = create_room_code()

    rooms[code] = {
        "code": code,
        "host": host,
        "hot_seat": hot_seat,
        "phase": "التسخين",
        "status": "waiting",
        "current_question": None,
        "question_number": 0,
        "questions": [],
        "participants": {},
        "created_at": datetime.utcnow().isoformat()
    }

    return jsonify({
        "success": True,
        "code": code
    })


# -------------------------------------------------
# حالة الغرفة
# -------------------------------------------------

@app.get("/api/rooms/<code>")
def room_info(code):
    room = get_room(code)

    if not room:
        return jsonify({"error": "الغرفة غير موجودة"}), 404

    return jsonify(room_state(room))


# -------------------------------------------------
# WebSocket
# -------------------------------------------------

@socketio.on("join_room")
def handle_join(data):
    code = str(data.get("code", "")).upper()
    name = str(data.get("name", "")).strip()

    room = get_room(code)

    if not room:
        emit("error_message", {
            "message": "الغرفة غير موجودة"
        })
        return

    if not name:
        emit("error_message", {
            "message": "اكتب اسمك أولًا"
        })
        return

    join_room(code)

    room["participants"][request.sid] = name

    emit("room_state", room_state(room), to=code)


@socketio.on("leave_room")
def handle_leave(data):
    code = str(data.get("code", "")).upper()

    room = get_room(code)

    if room and request.sid in room["participants"]:
        del room["participants"][request.sid]
        emit("room_state", room_state(room), to=code)


@socketio.on("submit_question")
def handle_question(data):
    code = str(data.get("code", "")).upper()
    question = str(data.get("question", "")).strip()
    name = str(data.get("name", "")).strip()

    room = get_room(code)

    if not room:
        emit("error_message", {
            "message": "الغرفة غير موجودة"
        })
        return

    if not question:
        emit("error_message", {
            "message": "اكتب السؤال"
        })
        return

    if len(question) > 500:
        emit("error_message", {
            "message": "السؤال طويل جدًا"
        })
        return

    room["question_number"] += 1

    item = {
        "id": room["question_number"],
        "name": name,
        "question": question,
        "status": "pending",
        "answer": ""
    }

    room["questions"].append(item)

    emit(
        "question_added",
        item,
        to=code
    )

    emit(
        "room_state",
        room_state(room),
        to=code
    )


@socketio.on("publish_question")
def handle_publish(data):
    code = str(data.get("code", "")).upper()
    question_id = int(data.get("question_id", 0))

    room = get_room(code)

    if not room:
        return

    question = next(
        (q for q in room["questions"]
         if q["id"] == question_id),
        None
    )

    if not question:
        return

    question["status"] = "published"
    room["current_question"] = question

    emit(
        "current_question",
        question,
        to=code
    )


@socketio.on("answer_question")
def handle_answer(data):
    code = str(data.get("code", "")).upper()
    answer = str(data.get("answer", "")).strip()

    room = get_room(code)

    if not room:
        return

    if not room["current_question"]:
        emit("error_message", {
            "message": "لا يوجد سؤال حالي"
        })
        return

    if not answer:
        emit("error_message", {
            "message": "اكتب الإجابة"
        })
        return

    room["current_question"]["answer"] = answer
    room["current_question"]["status"] = "answered"

    for q in room["questions"]:
        if q["id"] == room["current_question"]["id"]:
            q["answer"] = answer
            q["status"] = "answered"

    emit(
        "question_answered",
        room["current_question"],
        to=code
    )


@socketio.on("change_phase")
def handle_phase(data):
    code = str(data.get("code", "")).upper()
    phase = str(data.get("phase", "")).strip()

    room = get_room(code)

    if not room or not phase:
        return

    room["phase"] = phase

    emit(
        "phase_changed",
        {"phase": phase},
        to=code
    )


@socketio.on("change_status")
def handle_status(data):
    code = str(data.get("code", "")).upper()
    status = str(data.get("status", "")).strip()

    room = get_room(code)

    if not room:
        return

    room["status"] = status

    emit(
        "status_changed",
        {"status": status},
        to=code
    )


@socketio.on("disconnect")
def handle_disconnect():
    for code, room in rooms.items():
        if request.sid in room["participants"]:
            del room["participants"][request.sid]

            emit(
                "room_state",
                room_state(room),
                to=code
            )

            break


# -------------------------------------------------
# واجهة المستخدم
# -------------------------------------------------

PAGE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>🔥 الكرسي الساخن</title>

<script src="https://cdn.socket.io/4.8.1/socket.io.min.js"></script>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, Tahoma, sans-serif;
    background: #0b1020;
    color: white;
}

.container {
    width: min(900px, 94%);
    margin: auto;
    padding: 25px 0;
}

.hero {
    text-align: center;
    padding: 30px 15px;
}

.hero h1 {
    font-size: 42px;
    margin: 0 0 10px;
}

.hero p {
    color: #aeb7ca;
}

.card {
    background: #151c31;
    border: 1px solid #29334f;
    border-radius: 20px;
    padding: 20px;
    margin-bottom: 18px;
}

input,
textarea,
button {
    width: 100%;
    border-radius: 12px;
    padding: 13px;
    margin-top: 10px;
    font-size: 16px;
}

input,
textarea {
    background: #0d1427;
    color: white;
    border: 1px solid #34405e;
}

button {
    border: 0;
    background: #6d4aff;
    color: white;
    font-weight: bold;
    cursor: pointer;
}

button.secondary {
    background: #26324e;
}

button.danger {
    background: #b83250;
}

.hidden {
    display: none;
}

.badge {
    display: inline-block;
    padding: 7px 12px;
    border-radius: 20px;
    background: #26324e;
    margin: 4px;
}

.question {
    background: #0d1427;
    border-radius: 16px;
    padding: 16px;
    margin-top: 10px;
}

.question strong {
    color: #bcaeff;
}

.current {
    border: 2px solid #6d4aff;
}

.big {
    font-size: 25px;
    line-height: 1.7;
}

small {
    color: #8e9ab3;
}

</style>
</head>

<body>

<div class="container">

<div class="hero">
    <h1>🔥 الكرسي الساخن</h1>
    <p>منصة تفاعلية لإدارة لعبة الكرسي الساخن</p>
</div>

<div id="home" class="card">

    <h2>إنشاء غرفة</h2>

    <input
        id="hostName"
        placeholder="اسم المقدم">

    <input
        id="hotSeatName"
        placeholder="اسم صاحب الكرسي">

    <button onclick="createRoom()">
        🔥 إنشاء الغرفة
    </button>

    <hr>

    <h2>دخول غرفة</h2>

    <input
        id="joinCode"
        placeholder="رمز الغرفة">

    <input
        id="participantName"
        placeholder="اسمك">

    <button
        class="secondary"
        onclick="joinExistingRoom()">
        🚪 دخول
    </button>

</div>


<div id="room" class="hidden">

    <div class="card">

        <h2>
            🔥 غرفة:
            <span id="roomCode"></span>
        </h2>

        <p>
            🎤 صاحب الكرسي:
            <strong id="hotSeat"></strong>
        </p>

        <p>
            🎯 المرحلة:
            <span class="badge" id="phase"></span>
        </p>

        <p>
            الحالة:
            <span class="badge" id="status"></span>
        </p>

        <p>
            👥 المشاركون:
            <span id="participants"></span>
        </p>

    </div>


    <div class="card">

        <h2>📝 إرسال سؤال</h2>

        <textarea
            id="questionText"
            rows="4"
            placeholder="اكتب سؤالك هنا..."></textarea>

        <button onclick="sendQuestion()">
            إرسال السؤال 🔥
        </button>

    </div>


    <div class="card">

        <h2>🔥 السؤال الحالي</h2>

        <div
            id="currentQuestion"
            class="question big">
            لا يوجد سؤال منشور حاليًا
        </div>

        <textarea
            id="answerText"
            rows="4"
            placeholder="إجابة صاحب الكرسي..."></textarea>

        <button onclick="sendAnswer()">
            🎤 إرسال الإجابة
        </button>

    </div>


    <div class="card">

        <h2>📋 الأسئلة</h2>

        <div id="questions"></div>

    </div>

</div>

</div>


<script>

const socket = io();

let currentCode = "";
let currentName = "";
let state = null;


function createRoom() {

    const host =
        document.getElementById("hostName").value.trim();

    const hotSeat =
        document.getElementById("hotSeatName").value.trim();

    if (!host || !hotSeat) {
        alert("أدخل اسم المقدم وصاحب الكرسي");
        return;
    }

    fetch("/api/rooms", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            host: host,
            hot_seat: hotSeat
        })
    })
    .then(r => r.json())
    .then(data => {

        if (data.error) {
            alert(data.error);
            return;
        }

        currentCode = data.code;
        currentName = host;

        enterRoom();

        socket.emit("join_room", {
            code: currentCode,
            name: currentName
        });
    });
}


function joinExistingRoom() {

    const code =
        document.getElementById("joinCode")
        .value.trim()
        .toUpperCase();

    const name =
        document.getElementById("participantName")
        .value.trim();

    if (!code || !name) {
        alert("أدخل رمز الغرفة واسمك");
        return;
    }

    currentCode = code;
    currentName = name;

    fetch("/api/rooms/" + code)
    .then(r => r.json())
    .then(data => {

        if (data.error) {
            alert(data.error);
            return;
        }

        enterRoom();

        socket.emit("join_room", {
            code: code,
            name: name
        });
    });
}


function enterRoom() {

    document.getElementById("home")
        .classList.add("hidden");

    document.getElementById("room")
        .classList.remove("hidden");

    document.getElementById("roomCode")
        .textContent = currentCode;

    updateRoomLink();
}


function updateRoomLink() {

    const url =
        location.origin + "/?room=" + currentCode;

    history.replaceState(
        {},
        "",
        "/?room=" + currentCode
    );

    document.title =
        "🔥 الكرسي الساخن — " + currentCode;
}


function renderState(data) {

    state = data;

    document.getElementById("roomCode")
        .textContent = data.code;

    document.getElementById("hotSeat")
        .textContent = data.hot_seat;

    document.getElementById("phase")
        .textContent = data.phase;

    document.getElementById("status")
        .textContent = data.status;

    document.getElementById("participants")
        .textContent =
        data.participants.length;

    renderQuestions(data.questions);

    if (data.current_question) {
        renderCurrentQuestion(data.current_question);
    }
}


function renderQuestions(questions) {

    const box =
        document.getElementById("questions");

    box.innerHTML = "";

    questions.forEach(q => {

        const div =
            document.createElement("div");

        div.className = "question";

        div.innerHTML = `
            <strong>#${q.id} — ${escapeHtml(q.name)}</strong>
            <p>${escapeHtml(q.question)}</p>
            <small>الحالة: ${escapeHtml(q.status)}</small>
        `;

        box.appendChild(div);
    });
}


function renderCurrentQuestion(q) {

    const box =
        document.getElementById("currentQuestion");

    box.classList.add("current");

    box.innerHTML = `
        <small>
            السؤال #${q.id}
            — ${escapeHtml(q.name)}
        </small>
        <br>
        ${escapeHtml(q.question)}
    `;

    if (q.answer) {

        box.innerHTML += `
            <hr>
            <strong>🎤 الإجابة:</strong>
            <p>${escapeHtml(q.answer)}</p>
        `;
    }
}


function sendQuestion() {

    const text =
        document.getElementById("questionText")
        .value.trim();

    if (!text) {
        alert("اكتب السؤال");
        return;
    }

    socket.emit("submit_question", {
        code: currentCode,
        name: currentName,
        question: text
    });

    document.getElementById("questionText")
        .value = "";
}


function publishQuestion(id) {

    socket.emit("publish_question", {
        code: currentCode,
        question_id: id
    });
}


function sendAnswer() {

    const answer =
        document.getElementById("answerText")
        .value.trim();

    if (!answer) {
        alert("اكتب الإجابة");
        return;
    }

    socket.emit("answer_question", {
        code: currentCode,
        answer: answer
    });

    document.getElementById("answerText")
        .value = "";
}


socket.on("room_state", data => {
    renderState(data);
});


socket.on("question_added", q => {

    console.log("سؤال جديد:", q);

});


socket.on("current_question", q => {

    renderCurrentQuestion(q);

});


socket.on("question_answered", q => {

    renderCurrentQuestion(q);

});


socket.on("phase_changed", data => {

    document.getElementById("phase")
        .textContent = data.phase;

});


socket.on("status_changed", data => {

    document.getElementById("status")
        .textContent = data.status;

});


socket.on("error_message", data => {

    alert(data.message);

});


function escapeHtml(value) {

    const div =
        document.createElement("div");

    div.textContent = value;

    return div.innerHTML;
}


const params =
    new URLSearchParams(location.search);

const roomFromUrl =
    params.get("room");

if (roomFromUrl) {

    document.getElementById("joinCode")
        .value =
        roomFromUrl.toUpperCase();

}

</script>

</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=False
  )
