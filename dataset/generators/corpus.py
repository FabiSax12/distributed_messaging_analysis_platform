"""Phrase banks for the synthetic chats and voice notes.

Every harmful phrase has a ground-truth category decided by its meaning, not
by whether the text worker's rules catch it. Three kinds of entries make the
evaluation honest:

- ``DETECTABLE``: harmful phrasings that contain a rule keyword (expected TP).
- ``PARAPHRASE``: harmful phrasings with no rule keyword (expected FN).
- ``HARD_NEGATIVE``: harmless messages that contain a rule keyword (expected FP).

Voice notes use English only (the installed TTS voices are en-US); chats mix
Spanish (Costa Rican register) and English.
"""

from __future__ import annotations

THREAT = "threat"
VIOLENCE = "violence"
OFFENSIVE = "offensive_language"
ILLICIT = "illicit_activity"

SEVERITY = {THREAT: "high", VIOLENCE: "high", OFFENSIVE: "medium", ILLICIT: "high"}

FIRST_NAMES = [
    "Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie",
    "Andrea", "Mariana", "Diego", "Valeria", "Sofia", "Mateo", "Lucia", "Daniel",
    "Gabriela", "Andres", "Camila", "Jose", "Fernanda", "Pablo", "Natalia", "Kevin",
    "Paola", "Esteban", "Melissa", "Josue", "Tatiana", "Bryan",
]
LAST_NAMES = [
    "Placeholder", "Sample", "Example", "Rojas", "Mora", "Vargas", "Solano",
    "Jimenez", "Araya", "Quesada", "Castro", "Brenes", "Chaves", "Alfaro",
    "Navarro", "Calderon", "Ureña", "Salas",
]

SLOTS = {
    "time": ["7", "8", "9", "10", "3", "4", "5", "6", "7:30", "8:15", "noon"],
    "place_en": ["the mall", "the office", "the park", "school", "the gym", "the bus stop",
                 "the soda", "the clinic", "the library", "the stadium"],
    "place_es": ["el mall", "la oficina", "el parque", "el cole", "el gimnasio", "la parada",
                 "la soda", "la clínica", "la biblioteca", "el estadio"],
    "food_en": ["pizza", "gallo pinto", "tacos", "sushi", "empanadas", "rice and beans", "burgers"],
    "food_es": ["pizza", "gallo pinto", "tacos", "sushi", "empanadas", "arroz con pollo", "casado"],
    "day_en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "day_es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "amount": ["5", "10", "15", "20", "25", "40", "50", "100"],
    "thing_en": ["the charger", "my jacket", "the keys", "the notes", "the umbrella", "the cable",
                 "the report", "the tickets"],
    "thing_es": ["el cargador", "mi jacket", "las llaves", "los apuntes", "la sombrilla",
                 "el cable", "el informe", "las entradas"],
}

BENIGN_EN = [
    "are you coming tomorrow?", "ok see you at {time}", "I'm at {place_en} already",
    "who has {thing_en}?", "lol", "haha true", "thanks!", "good morning everyone",
    "can we move it to {day_en}?", "running 10 minutes late, sorry", "sounds good",
    "anyone want {food_en} tonight?", "I left {thing_en} at {place_en}",
    "did you finish the assignment?", "happy birthday!! 🎉", "on my way",
    "the meeting is at {time} at {place_en}", "send me the location please",
    "can someone pick me up at {place_en}?", "I owe you {amount} for the {food_en}",
    "great job today team", "the bus is full again", "it's raining a lot here",
    "don't forget to bring {thing_en}", "ok", "👍", "perfect", "see you {day_en}",
    "my phone is almost dead", "what time does it close?", "I can't make it {day_en}",
    "let's vote for the place", "who's in for {day_en}?", "nice photo",
    "I'll call you later", "where are you?", "already paid, thanks",
    "the teacher moved the exam to {day_en}", "is the gym open on {day_en}?",
    "we need {amount} more people", "check your email", "good night",
]
BENIGN_ES = [
    "¿vienen mañana?", "ok nos vemos a las {time}", "ya estoy en {place_es}",
    "¿quién tiene {thing_es}?", "jajaja", "jaja sí", "¡gracias!", "buenos días a todos",
    "¿lo pasamos para el {day_es}?", "voy 10 minutos tarde, perdón", "pura vida",
    "¿alguien quiere {food_es} hoy?", "dejé {thing_es} en {place_es}",
    "¿terminaron la tarea?", "¡feliz cumple!! 🎉", "voy en camino",
    "la reunión es a las {time} en {place_es}", "mándame la ubicación porfa",
    "¿alguien me recoge en {place_es}?", "te debo {amount} mil del {food_es}",
    "excelente trabajo hoy", "el bus viene llenísimo", "está lloviendo durísimo",
    "no se les olvide {thing_es}", "ok", "👍", "diay sí", "nos vemos el {day_es}",
    "tengo el cel casi muerto", "¿a qué hora cierran?", "no puedo el {day_es}",
    "votemos por el lugar", "¿quién se apunta el {day_es}?", "qué buena foto",
    "ahorita te llamo", "¿dónde estás?", "ya pagué, gracias",
    "la profe pasó el examen para el {day_es}", "¿el gym abre el {day_es}?",
    "faltan {amount} personas", "revisen el correo", "buenas noches", "tuanis",
]

# Multi-line benign messages (WhatsApp continuation lines).
MULTILINE_EN = [
    "shopping list:\n- rice\n- beans\n- coffee",
    "reminder for {day_en}:\nbring {thing_en}\nand the signed form",
    "address is\n200m north of {place_en}\nblue house",
]
MULTILINE_ES = [
    "lista del súper:\n- arroz\n- frijoles\n- café",
    "recordatorio para el {day_es}:\ntraer {thing_es}\ny el formulario firmado",
    "la dirección es\n200m norte de {place_es}\ncasa azul",
]

DETECTABLE = {
    THREAT: {
        "en": ["if you tell anyone, you will regret it", "pay by {day_en} or I will hurt you",
               "I swear I will kill you if you show up", "keep talking and you will regret it",
               "I know people who can hurt you"],
        "es": ["te voy a matar si abrís la boca", "vas a pagar por lo que hiciste",
               "te vas a arrepentir de haber hablado", "si no pagás el {day_es}, vas a pagar caro",
               "te voy a matar, ya sabés"],
    },
    VIOLENCE: {
        "en": ["we should stab him when he leaves {place_en}", "they want to shoot the guy from the store",
               "let's give him a beating after class", "bring the bat, the beating is tonight"],
        "es": ["le vamos a dar una golpiza saliendo de {place_es}", "hay que golpear al nuevo",
               "dicen que lo van a apuñalar", "le van a disparar al del carro rojo"],
    },
    OFFENSIVE: {
        "en": ["you are such an idiot", "shut up, stupid", "what an asshole you are",
               "only an idiot would do that", "stop being stupid and answer"],
        "es": ["sos un imbécil", "qué idiota sos", "esto es una mierda igual que vos",
               "callate, imbécil", "sos el más idiota del grupo"],
    },
    ILLICIT: {
        "en": ["who wants to sell drugs at the party?", "the drug deal is at {time} behind {place_en}",
               "the extortion money goes to the usual account", "we can clean it with money laundering through the shop"],
        "es": ["vamos a vender drogas en {place_es}", "la venta de drogas es a las {time}",
               "lo de la extorsión se paga el {day_es}", "el lavado de dinero va por la soda"],
    },
}

PARAPHRASE = {
    THREAT: {
        "en": ["I know where your kids go to school", "you won't see next week if you talk",
               "watch your back on the way home", "nobody will find you if this goes wrong"],
        "es": ["sé dónde vivís, cuidate", "esto no se queda así, ya vas a ver",
               "cuidado cuando salgás solo", "nadie te va a encontrar si hablás"],
    },
    VIOLENCE: {
        "en": ["let's break his legs on {day_en}", "we'll jump him at {place_en}"],
        "es": ["le partimos la cara el {day_es}", "lo esperamos en {place_es} para darle"],
    },
    OFFENSIVE: {
        "en": ["you're a worthless clown", "nobody here can stand you, loser"],
        "es": ["sos un inútil, payaso", "qué asco de persona sos"],
    },
    ILLICIT: {
        "en": ["I have the white stuff, {amount} a gram", "the package from the port arrives {day_en}, cash only"],
        "es": ["tengo la mercancía, a {amount} mil el gramo", "el paquete del puerto llega el {day_es}, solo efectivo"],
    },
}

# Harmless messages that contain a rule keyword; labeled negative.
HARD_NEGATIVE = {
    "en": ["the photo shoot is at {time} at {place_en}", "this stupid printer jammed again",
           "I'm going to kill you at chess tonight haha", "watched a documentary about a drug deal investigation",
           "the dentist said it won't hurt you, relax"],
    "es": ["la película de la golpiza del boxeo estuvo buenísima", "el entrenador dijo que hay que golpear la bola más fuerte",
           "el documental sobre lavado de dinero estuvo interesante", "se me cayó el café, qué idiota yo jaja"],
}

SYSTEM_WHATSAPP = [
    "Messages and calls are end-to-end encrypted. No one outside of this chat, not even WhatsApp, can read or listen to them.",
    "{a} created group \"{group}\"",
    "{a} added {b}",
    "{a} changed the subject to \"{group}\"",
    "{b} left",
    "{a} changed this group's icon",
]
SYSTEM_TELEGRAM = ["create_group", "invite_members", "pin_message", "edit_group_title"]

# ---- Voice-note sentences (English, synthetic speech) ---------------------- #

VOICE_BENIGN = [
    "Hey, it's me, just calling to check if you're coming tomorrow.",
    "I'm at the supermarket, do you need anything?",
    "The meeting moved to Thursday at three.",
    "Can you send me the address again? I lost the message.",
    "Traffic is terrible today, I'll be about twenty minutes late.",
    "Thanks for the help yesterday, I really appreciate it.",
    "Don't forget to bring the charger and the notes.",
    "I'm going to the gym after work, want to come?",
    "My phone is almost out of battery, call me later.",
    "The teacher said the exam is on Monday.",
    "We need two more people for the football match on Saturday.",
    "I already paid for the tickets, you can pay me back later.",
    "It's raining a lot here, take an umbrella.",
    "The bus never came, so I'm walking home.",
    "Good morning everyone, have a great day.",
    "Let me know when you arrive.",
    "I left the keys with the neighbor.",
    "We should order pizza tonight.",
    "I finished the report and sent it by email.",
    "Happy birthday, I hope you have an amazing day.",
    "The store closes at nine, so hurry up.",
    "Can you pick up my sister from school?",
    "I'll call you back in five minutes.",
    "The doctor's appointment is next Wednesday.",
    "Remember to water the plants while I'm away.",
    "We can meet at the park near the library.",
    "I saw your message, everything is fine.",
    "The internet is down again at home.",
    "Let's review the project tomorrow morning.",
    "Tell mom I'll be home for dinner.",
]
VOICE_DETECTABLE = {
    THREAT: ["If you tell anyone about this, you will regret it.",
             "Pay me by Friday or I will hurt you.",
             "I swear I will kill you if you come back here."],
    VIOLENCE: ["We are going to stab him when he leaves the bar.",
               "They plan to shoot the guy from the store tonight."],
    OFFENSIVE: ["You are such an idiot, I can't believe it.",
                "Stop being stupid and answer the phone."],
    ILLICIT: ["The drug deal is tonight behind the stadium.",
              "We need to sell drugs at the party on Saturday."],
}
VOICE_PARAPHRASE = {
    THREAT: ["I know where your kids go to school.",
             "Watch your back on the way home tonight."],
    ILLICIT: ["I have the white stuff, twenty a gram, cash only."],
}
VOICE_HARD_NEGATIVE = [
    "I'm going to kill you at chess tonight, get ready.",
    "The photo shoot starts at ten, don't be late.",
]
