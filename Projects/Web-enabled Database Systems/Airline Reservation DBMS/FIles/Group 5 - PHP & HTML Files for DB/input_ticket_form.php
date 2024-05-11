<!DOCTYPE html>
<html>
<body>
<a href="index.html">Home</a>
<div style="width: 20px;"></div>
<?php

$ticket_id = $_POST['ticket_id'];
$seat_num = $_POST['seat_num'];
$date = $_POST['date'];
$user_id = $_POST['user_id'];
$flight_num = $_POST['flight_num']; // Moved flight_num after user_id
$transaction_id = $_POST['transaction_id'];
$amount = $_POST['amount'];
$payment_mode = $_POST['payment_mode'];


$host = "pluto.hood.edu";
$dbname = "ajo2db";
$user = "ajo2";
$pass = "password";

try {
    $conn = new PDO("mysql:host=$host;dbname=$dbname", $user, $pass);
    $conn->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

   
    $sql = "INSERT INTO ticket_payment (ticket_id, seat_num, date, user_id, flight_num, transaction_id, amount, payment_mode) 
            VALUES (:ticket_id, :seat_num, :date, :user_id, :flight_num, :transaction_id, :amount, :payment_mode)";
    $stmt = $conn->prepare($sql);

    $stmt->bindParam(':ticket_id', $ticket_id);
    $stmt->bindParam(':seat_num', $seat_num);
    $stmt->bindParam(':date', $date);
    $stmt->bindParam(':user_id', $user_id);
    $stmt->bindParam(':flight_num', $flight_num);
    $stmt->bindParam(':transaction_id', $transaction_id);
    $stmt->bindParam(':amount', $amount);
    $stmt->bindParam(':payment_mode', $payment_mode);

    
    if ($stmt->execute()) {
        $rows_affected = $stmt->rowCount();
        echo "<h2>".$rows_affected." Reservation Made Successfully!</h2>";
    } else {
        echo "Insertion failed: (" . $conn->errorCode() . ") " . implode(" ", $conn->errorInfo());
    }

    $conn = null;
} catch (PDOException $e) {
    die("Could not connect to the database $dbname :" . $e->getMessage());
}
?>

</body>
</html>

<footer>
        <p>&copy; 2024 TravelX Booking. All rights reserved.</p>
    </footer>
