import QtQuick

Item {
    id: root
    // Убираем фиксированные размеры, чтобы SizeRootObjectToView работал корректно
    
    // Свойства для управления из Python
    property real animRadius: 20
    property real animOpacity: 0.8
    property real animScale: 1.0
    property real rotX: 0
    property real rotY: 0
    property real rotZ: 0
    property real offsetX: 0
    property real offsetY: 0
    property real offsetZ: 0
    
    // Добавляем недостающие свойства, которые передает Python
    property real rectWidth: 200
    property real rectHeight: 100

    Rectangle {
        id: body
        // Устанавливаем размеры из Python-свойств
        width: root.rectWidth
        height: root.rectHeight
        
        // Чтобы offsetX/Y работали, используем якорь для центра, 
        // но добавляем смещение через margin или убираем якоря совсем
        anchors.centerIn: parent
        anchors.horizontalCenterOffset: root.offsetX
        anchors.verticalCenterOffset: root.offsetY

        color: "#1a1a1a"
        radius: root.animRadius
        opacity: root.animOpacity
        border.color: "#33ffffff"
        border.width: 1

        scale: root.animScale

        transform: [
            Rotation { 
                axis { x: 1; y: 0; z: 0 } 
                angle: root.rotX 
                origin.x: body.width / 2
                origin.y: body.height / 2
            },
            Rotation { 
                axis { x: 0; y: 1; z: 0 } 
                angle: root.rotY 
                origin.x: body.width / 2
                origin.y: body.height / 2
            },
            Rotation { 
                axis { x: 0; y: 0; z: 1 } 
                angle: root.rotZ 
                origin.x: body.width / 2
                origin.y: body.height / 2
            }
        ]
    }
}