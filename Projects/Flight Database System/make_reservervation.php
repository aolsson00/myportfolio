php
<?php
// Database connection
$servername = "pluto.hood.edu";
$username = "mbs17";
$password = "password";
$dbname = "mbs17db";

// Create connection
$conn = new mysqli($servername, $username, $password, $dbname);

// Check connection
if ($conn->connect_error) {
    die("Connection failed: " . $conn->connect_error);
}

// Retrieve form data
$passenger_id = $_POST['passenger_id'];
$flight_num = $_POST['flight_num'];
$seat_num = $_POST['seat_num'];
$date = $_POST['date'];
$amount = $_POST['amount'];
$payment_mode = $_POST['payment_mode'];

// SQL query to insert reservation data into ticket_payment table
$sql = "INSERT INTO ticket_payment (passenger_id, flight_num, seat_num, date, amount, payment_mode) 
        VALUES ('$passenger_id', '$flight_num', '$seat_num', '$date', '$amount', '$payment_mode')";

if ($conn->query($sql) === TRUE) {
    echo "Reservation successful. Ticket payment record added.";
} else {
    echo "Error: " . $sql . "<br>" . $conn->error;
}

$conn->close();
?>

