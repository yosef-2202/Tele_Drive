document.addEventListener('DOMContentLoaded', () => {
    // Auto-dismiss alerts sau 3 giây
    setTimeout(function() {
        let alerts = document.querySelectorAll('.alert');
        alerts.forEach(function(alert) {
            if (typeof bootstrap !== 'undefined') {
                let bsAlert = new bootstrap.Alert(alert);
                bsAlert.close();
            } else {
                alert.style.display = 'none';
            }
        });
    }, 3000);

    // Dọn dẹp video khi đóng Modal để tiết kiệm băng thông và RAM
    const videoModalEl = document.getElementById('videoModal');
    if(videoModalEl) {
        videoModalEl.addEventListener('hidden.bs.modal', function () {
            const videoEl = document.getElementById('previewVideo');
            videoEl.pause();
            videoEl.src = ""; 
            videoEl.load();
        });
    }
});

/**
 * Mở Media Viewer (Hình ảnh/Video)
 */
function openMediaViewer(type, fileId, name, size, date) {
    if (type === 'image') {
        document.getElementById('previewImage').src = `/download/${fileId}`;
        document.getElementById('imgName').innerText = name;
        document.getElementById('imgSize').innerText = size;
        document.getElementById('imgDate').innerText = date;
        new bootstrap.Modal(document.getElementById('imageModal')).show();
    } else if (type === 'video') {
        const videoEl = document.getElementById('previewVideo');
        videoEl.src = `/stream/${fileId}`; // Trỏ đến API stream video
        document.getElementById('videoName').innerText = name;
        new bootstrap.Modal(document.getElementById('videoModal')).show();
        videoEl.play();
    }
}