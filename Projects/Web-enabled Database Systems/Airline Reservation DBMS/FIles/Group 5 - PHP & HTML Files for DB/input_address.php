<?php
include_once 'db.php';

$passenger_id = $_POST['passenger_id'];
$address = $_POST['address'];

try {
    $conn->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);


    $checkStmt = $conn->prepare("SELECT passenger_id FROM passenger WHERE passenger_id = :passenger_id");
    $checkStmt->bindParam(':passenger_id', $passenger_id);
    $checkStmt->execute();

    if ($checkStmt->rowCount() > 0) {

        $stmt = $conn->prepare("INSERT INTO passenger_address (passenger_id, address) VALUES (:passenger_id, :address)");
        $stmt->bindParam(':passenger_id', $passenger_id);
        $stmt->bindParam(':address', $address);

        $stmt->execute();

        if ($stmt->rowCount() > 0) {
            echo "<h2>Passenger address added successfully!</h2>";
            echo "Passenger ID: " . $passenger_id . "<br>";
            echo "Address: " . $address . "<br>";
        } else {
            echo "<h2>Failed to add passenger address.</h2>";
        }
    } else {
        echo "<h2>Passenger ID does not exist. Please enter a valid passenger ID.</h2>";
    }
} catch(PDOException $e) {
    echo "Error: " . $e->getMessage();
}

$conn = null; 
?>

<footer>
        <p>&copy; 2024 TravelX Booking. All rights reserved.</p>
    </footer>